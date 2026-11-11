"""
Yargıtay Karar Arama Otomasyonu ve Arşivleyici

Bu modül, Yargıtay Karar Arama Portalını (karararama.yargitay.gov.tr) Selenium
ve Chromium kullanarak otomatikleştirir; belirlenen tarih aralıklarını (chunk)
adım adım sorgular, DataTables tablosundaki kararları ve detay metinlerini
JSON formatında yerel diske arşivler.

Temel Özellikler:
- Tarih dilimleme (Varsayılan 5 günlük adımlar) ve güncelden geçmişe tarama
- SQLite (archive_state.db) veritabanı ile çift katmanlı durum ve ilerleme takibi
- O(1) karmaşıklığında bellek tabanlı ID kontrolü ile mükerrer indirmeleri önleme
- Chromium headless (arka plan) ve normal GUI modları desteği
- Oturum içi asenkron JavaScript / fetch istekleri ile güvenli doküman çekme
- Yıl bazlı hiyerarşik dizin yapısı
"""

import os
import sys
import time
import json
import random
import re
import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple, Optional

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options


class YargitayScraper:
    """
    Yargıtay Karar Arama portalı üzerinde tarih filtreli sorgular yürüten,
    sayfaları gezen ve kararları arşivleyen ana sınıf.
    """

    def __init__(
        self,
        output_dir: str = "yargitayjson",
        start_date_str: str = "01.01.2020",
        end_date_str: str = "16.10.2026",
        chunk_days: int = 5,
        headless: bool = True
    ):
        self.output_dir = os.path.abspath(output_dir)
        os.makedirs(self.output_dir, exist_ok=True)

        self.start_date = datetime.strptime(start_date_str, "%d.%m.%Y")
        self.end_date = datetime.strptime(end_date_str, "%d.%m.%Y")
        self.chunk_days = chunk_days
        self.headless = headless

        self.db_path = os.path.join(self.output_dir, "archive_state.db")
        self.init_database()

        # Daha önce indirilmiş karar ID'lerini tutan küme (O(1) arama hızı için)
        self.downloaded_doc_ids = set()
        self.load_known_ids()

        self.driver = None

    def log(self, msg: str, tag: str = "BİLGİ"):
        """
        Zaman damgalı standart konsol çıktısı üretir.
        """
        zaman = time.strftime("%H:%M:%S")
        print(f"[{zaman}] [{tag}] {msg}", flush=True)

    def init_database(self):
        """
        SQLite üzerinde durum takip tablolarını oluşturur.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS indirilen_kararlar (
                    dokuman_id TEXT PRIMARY KEY,
                    global_id INTEGER,
                    yil TEXT,
                    dosya_adi TEXT,
                    eklenme_tarihi TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tamamlanan_araliklar (
                    baslangic TEXT,
                    bitis TEXT,
                    karar_sayisi INTEGER,
                    tamamlanma_tarihi TEXT,
                    PRIMARY KEY(baslangic, bitis)
                )
            """)
            conn.commit()

    def load_known_ids(self):
        """
        Veritabanından ve diskteki mevcut dosyalardan bilinen doküman ID'lerini belleğe yükler.
        """
        self.log("Mevcut arşiv taranıyor ve indeksleniyor...")

        # 1. SQLite veritabanından ID'leri oku
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT dokuman_id FROM indirilen_kararlar")
            rows = cursor.fetchall()
            for r in rows:
                self.downloaded_doc_ids.add(str(r[0]))

        # 2. Veritabanı boşsa diskteki JSON dosyalarını tara ve veritabanına ekle
        if len(self.downloaded_doc_ids) == 0:
            self.log("Veritabanı yeni, mevcut JSON dosyaları taranıp veritabanına aktarılıyor...")
            bulunanlar = []
            max_id = 0
            for root, _, files in os.walk(self.output_dir):
                for f in files:
                    if f.startswith("karar_") and f.endswith(".json"):
                        try:
                            gid = int(f.replace("karar_", "").replace(".json", ""))
                            if gid > max_id:
                                max_id = gid
                        except ValueError:
                            gid = 0
                        fpath = os.path.join(root, f)
                        try:
                            with open(fpath, "r", encoding="utf-8") as jf:
                                d = json.load(jf)
                                doc_id = str(d.get("dokuman_id") or d.get("id") or "")
                                yil = str(d.get("kayit_yili_klasoru") or "2026")
                                if doc_id:
                                    self.downloaded_doc_ids.add(doc_id)
                                    bulunanlar.append((doc_id, gid, yil, f, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                        except Exception:
                            pass

            if bulunanlar:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.executemany("""
                        INSERT OR IGNORE INTO indirilen_kararlar (dokuman_id, global_id, yil, dosya_adi, eklenme_tarihi)
                        VALUES (?, ?, ?, ?, ?)
                    """, bulunanlar)
                    conn.commit()
                self.log(f"Önceden indirilmiş {len(bulunanlar)} adet karar veritabanına indekslendi.")

        self.log(f"İndeksleme tamamlandı. Toplam {len(self.downloaded_doc_ids)} adet benzersiz karar hazır.")

    def get_max_global_id(self) -> int:
        """
        Kaldığı en büyük genel sıra numarasını döndürür.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(global_id) FROM indirilen_kararlar")
            res = cursor.fetchone()
            if res and res[0] is not None:
                return int(res[0])
        return 0

    def is_chunk_completed(self, b_str: str, s_str: str) -> bool:
        """
        İlgili tarih aralığının daha önce taranıp taranmadığını kontrol eder.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM tamamlanan_araliklar WHERE baslangic = ? AND bitis = ?", (b_str, s_str))
            return cursor.fetchone() is not None

    def mark_chunk_completed(self, b_str: str, s_str: str, count: int):
        """
        Tamamlanan tarih aralığını veritabanına kaydeder.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO tamamlanan_araliklar (baslangic, bitis, karar_sayisi, tamamlanma_tarihi)
                VALUES (?, ?, ?, ?)
            """, (b_str, s_str, count, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()

    def driver_baslat(self):
        """
        Selenium Chrome/Chromium sürücüsünü yapılandırır ve başlatır.
        """
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass

        self.log("Tarayıcı oturumu başlatılıyor...", "TARAYICI")
        chrome_options = Options()
        if self.headless:
            chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )

        try:
            self.driver = webdriver.Chrome(options=chrome_options)
        except Exception:
            # Sistem yolunda chromedriver varsa service ile başlatmayı dene
            self.driver = webdriver.Chrome(service=Service(), options=chrome_options)

        self.driver.set_page_load_timeout(40)
        self.driver.set_script_timeout(40)

    def close_driver(self):
        """
        Açık olan tarayıcı oturumunu güvenle kapatır.
        """
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

    def tarihe_gore_ara(self, b_str: str, s_str: str) -> Tuple[bool, int]:
        """
        Belirtilen iki tarih aralığında detaylı arama formunu doldurarak sorgular.
        Dönen değer: (başarılı_mı, toplam_sonuç_sayısı)
        """
        for deneme in range(1, 4):
            try:
                self.log(f"Arama yapılıyor: {b_str} - {s_str} (Deneme {deneme}/3)...")
                self.driver.get("https://karararama.yargitay.gov.tr/index")
                time.sleep(2)

                # Sayfa içi JavaScript müdahalesi ile arama formunu tetikle
                self.driver.execute_script(f"""
                    window.moveNext = function() {{}};
                    let detay = document.getElementById('detay');
                    if (detay) detay.click();
                    let b = document.getElementById('kararTarihiIlk');
                    let s = document.getElementById('kararTarihiSon');
                    if (b) b.value = '{b_str}';
                    if (s) s.value = '{s_str}';
                    let btn = document.getElementById('detaylıAramaG');
                    if (btn) btn.click();
                """)

                # Tablonun ve toplam sayının yüklenmesini bekle
                t0 = time.time()
                while time.time() - t0 < 15:
                    status = self.driver.execute_script("""
                        let t = window.jQuery && jQuery('#detayAramaSonuclar').DataTable ? jQuery('#detayAramaSonuclar').DataTable() : null;
                        let totText = jQuery('#toplamSonuc').text().trim();
                        let emptyMsg = jQuery('#detayAramaSonuclar .dataTables_empty').length > 0;
                        let zeroRecords = totText === '0';
                        if (t && t.page.info()) {
                            return {
                                ready: true,
                                total: t.page.info().recordsTotal,
                                pages: t.page.info().pages
                            };
                        }
                        if (zeroRecords || emptyMsg) {
                            return { ready: true, total: 0, pages: 0 };
                        }
                        return { ready: false };
                    """)
                    if status and status.get("ready"):
                        tot = status.get("total", 0)
                        self.log(f"Arama sonucu: {tot} karar bulundu.")
                        return True, tot
                    time.sleep(0.5)

                self.log("Arama zaman aşımına uğradı, yeniden deneniyor...", "UYARI")
            except Exception as e:
                self.log(f"Arama sırasında hata: {e}", "HATA")
                time.sleep(2)

        return False, 0

    def tekil_dokuman_indir(self, doc_id: str) -> Optional[str]:
        """
        Tarayıcı oturumu bağlamında doküman içeriğini çeker (yeniden deneme korumalı).
        """
        for deneme in range(4):
            try:
                res = self.driver.execute_async_script("""
                    const callback = arguments[arguments.length - 1];
                    fetch('/getDokuman?id=' + arguments[0])
                        .then(r => r.text())
                        .then(t => callback({status: 200, text: t}))
                        .catch(e => callback({status: 0, text: String(e)}));
                """, doc_id)

                if res and res.get("status") == 200:
                    text = res.get("text", "")
                    low = text.lower()
                    if "erişim sınırı" in low or "güvenlik politikaları" in low or "sınıra ulaştın" in low:
                        self.log(f"Erişim sınırı algılandı, 25 saniye bekleniyor... (Deneme {deneme+1}/4)", "UYARI")
                        time.sleep(25)
                        continue
                    return text
            except Exception as ex:
                self.log(f"İndirme isteğinde hata: {ex}", "UYARI")

            time.sleep(1.5)

        return None

    def sayfadaki_kararlari_cek_ve_kaydet(self, sayfa_no: int, global_counter: int) -> Tuple[int, int]:
        """
        Aktif sayfadaki kararları sırayla çeker ve JSON olarak kaydeder.
        Dönen değer: (indirilen_yeni_karar_adedi, güncellenmiş_global_counter)
        """
        rows_data = self.driver.execute_script("""
            let t = jQuery('#detayAramaSonuclar').DataTable();
            return t ? t.rows({ page: 'current' }).data().toArray() : [];
        """)

        if not rows_data:
            return 0, global_counter

        yeni_indirilen = 0
        db_records = []

        for row_meta in rows_data:
            doc_id = str(row_meta.get("id") or "")
            if not doc_id:
                continue

            if doc_id in self.downloaded_doc_ids:
                continue

            content_text = self.tekil_dokuman_indir(doc_id)
            if not content_text:
                continue

            global_counter += 1

            try:
                karar_json = json.loads(content_text)
            except json.JSONDecodeError:
                karar_json = {"id": doc_id, "ham_icerik": content_text}

            # Karar yılı tespiti
            tarih_val = str(row_meta.get("kararTarihi") or karar_json.get("kararTarihi") or "")
            match = re.search(r'\b(20[0-2][0-9]|19[8-9][0-9])\b', tarih_val + str(karar_json))
            karar_yili = match.group(1) if match else "2026"

            yil_klasoru = os.path.join(self.output_dir, str(karar_yili))
            os.makedirs(yil_klasoru, exist_ok=True)

            dosya_adi = f"karar_{global_counter}.json"
            dosya_yolu = os.path.join(yil_klasoru, dosya_adi)

            karar_json["global_id"] = global_counter
            karar_json["dokuman_id"] = doc_id
            karar_json["daire"] = row_meta.get("daire", "")
            karar_json["esasNo"] = row_meta.get("esasNo", "")
            karar_json["kararNo"] = row_meta.get("kararNo", "")
            karar_json["kararTarihi"] = row_meta.get("kararTarihi", "")
            karar_json["kayit_yili_klasoru"] = str(karar_yili)
            karar_json["indirme_zamani"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            with open(dosya_yolu, "w", encoding="utf-8") as f_out:
                json.dump(karar_json, f_out, ensure_ascii=False, indent=2)

            self.downloaded_doc_ids.add(doc_id)
            db_records.append((doc_id, global_counter, str(karar_yili), dosya_adi, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            yeni_indirilen += 1

            time.sleep(random.uniform(0.15, 0.35))

        if db_records:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.executemany("""
                    INSERT OR IGNORE INTO indirilen_kararlar (dokuman_id, global_id, yil, dosya_adi, eklenme_tarihi)
                    VALUES (?, ?, ?, ?, ?)
                """, db_records)
                conn.commit()

        self.log(f"Sayfa {sayfa_no}: {yeni_indirilen} yeni karar kaydedildi | Toplam Benzersiz: {len(self.downloaded_doc_ids)}")
        return yeni_indirilen, global_counter

    def sonraki_sayfaya_gec(self) -> bool:
        """
        DataTables tablosunu sonraki sayfaya geçirir ve AJAX isteğinin tamamlanmasını bekler.
        """
        try:
            can_advance = self.driver.execute_script("""
                let t = jQuery('#detayAramaSonuclar').DataTable();
                if (!t) return false;
                let info = t.page.info();
                if (info.page < info.pages - 1) {
                    window._ajax_done = false;
                    jQuery('#detayAramaSonuclar').one('xhr.dt', function() {
                        window._ajax_done = true;
                    });
                    t.page('next').draw('page');
                    return true;
                }
                return false;
            """)

            if not can_advance:
                return False

            t0 = time.time()
            while time.time() - t0 < 15:
                done = self.driver.execute_script("return window._ajax_done;")
                if done:
                    return True
                time.sleep(0.2)

            return True
        except Exception as e:
            self.log(f"Sonraki sayfaya geçişte hata: {e}", "UYARI")
            return False

    def araliklari_olustur(self) -> List[Tuple[str, str]]:
        """
        Başlangıç ve bitiş arasındaki tarihleri chunk adımları halinde listeler (güncelden geçmişe doğru).
        """
        araliklar = []
        curr = self.end_date
        while curr > self.start_date:
            chunk_start = max(self.start_date, curr - timedelta(days=self.chunk_days))
            b_str = chunk_start.strftime("%d.%m.%Y")
            s_str = curr.strftime("%d.%m.%Y")
            araliklar.append((b_str, s_str))
            curr = chunk_start - timedelta(days=1)
        return araliklar

    def calistir(self, hedef_toplam_karar: int = 10000000):
        """
        Tüm tarih aralıklarını sırayla işleyen ana yürütücü fonksiyon.
        """
        self.log("=" * 60)
        self.log(f"Yargıtay Arşivleme Motoru Başlatılıyor (Hedef: {hedef_toplam_karar:,} Karar)")
        self.log(f"Tarih Aralığı: {self.start_date.strftime('%d.%m.%Y')} -> {self.end_date.strftime('%d.%m.%Y')} (Dilim: {self.chunk_days} gün)")
        self.log("=" * 60)

        global_counter = self.get_max_global_id()
        self.log(f"Kaldığı global numara: #{global_counter} (Mevcut Benzersiz Karar: {len(self.downloaded_doc_ids)})")

        araliklar = self.araliklari_olustur()
        self.log(f"Toplam {len(araliklar)} adet tarih dilimi işlenecek.")

        for idx, (b_str, s_str) in enumerate(araliklar, 1):
            if len(self.downloaded_doc_ids) >= hedef_toplam_karar:
                self.log("[BİLGİ] Hedef karar sayısına başarıyla ulaşıldı.")
                break

            if self.is_chunk_completed(b_str, s_str):
                self.log(f"[{idx}/{len(araliklar)}] {b_str} - {s_str} dilimi daha önce tamamlanmış, atlanıyor.")
                continue

            self.log(f"\nDilim [{idx}/{len(araliklar)}]: {b_str} - {s_str} taranıyor...")

            self.driver_baslat()

            try:
                ok, total_count = self.tarihe_gore_ara(b_str, s_str)
                if not ok or total_count == 0:
                    self.log(f"{b_str} - {s_str} aralığında karar bulunamadı veya sonuç boş döndü.")
                    self.mark_chunk_completed(b_str, s_str, 0)
                    continue

                sayfa_sayisi = (total_count + 9) // 10
                self.log(f"Toplam {total_count} karar ({sayfa_sayisi} sayfa) indirilecek.")

                chunk_indirilen = 0
                mevcut_sayfa = 1

                while mevcut_sayfa <= sayfa_sayisi:
                    yeni_sayi, global_counter = self.sayfadaki_kararlari_cek_ve_kaydet(mevcut_sayfa, global_counter)
                    chunk_indirilen += yeni_sayi

                    if mevcut_sayfa >= sayfa_sayisi:
                        break

                    if not self.sonraki_sayfaya_gec():
                        self.log("Sonraki sayfaya geçilemedi (Son sayfaya ulaşıldı veya sınır).", "UYARI")
                        break

                    mevcut_sayfa += 1
                    time.sleep(random.uniform(0.3, 0.6))

                self.mark_chunk_completed(b_str, s_str, chunk_indirilen)
                self.log(f"Dilim tamamlandı: {b_str} - {s_str} (İndirilen: {chunk_indirilen} karar)\n")

            except Exception as e:
                self.log(f"Dilim taranırken beklenmedik hata meydana geldi: {e}", "HATA")
                time.sleep(5)
            finally:
                self.close_driver()
                time.sleep(1)

        self.log("=" * 60)
        self.log(f"Arşivleme işlemi tamamlandı. Toplam benzersiz karar: {len(self.downloaded_doc_ids)}")
        self.log("=" * 60)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Yargıtay Karar Arama Otomasyonu")
    parser.add_argument("--output", default="yargitayjson", help="Hedef çıktı klasörü")
    parser.add_argument("--start", default="01.01.2020", help="Başlangıç tarihi (GG.AA.YYYY)")
    parser.add_argument("--end", default="16.10.2026", help="Bitiş tarihi (GG.AA.YYYY)")
    parser.add_argument("--chunk-days", type=int, default=5, help="Her sorgudaki gün aralığı (varsayılan: 5 gün)")
    parser.add_argument("--gui", action="store_true", help="Tarayıcıyı ekranda görünür çalıştır")
    parser.add_argument("--max", type=int, default=10000000, help="Hedef maksimum karar sayısı")

    args = parser.parse_args()

    bot = YargitayScraper(
        output_dir=args.output,
        start_date_str=args.start,
        end_date_str=args.end,
        chunk_days=args.chunk_days,
        headless=not args.gui
    )
    bot.calistir(hedef_toplam_karar=args.max)
