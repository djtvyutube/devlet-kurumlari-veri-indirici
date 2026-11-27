"""
Kamu Kurumları Belge ve PDF Rapor İndirici

Sayıştay ve diğer kamu sitelerindeki kategori/rapor sayfalarını tarayarak
PDF dosyalarını, detay sayfasının ham HTML kaynağını ve hiçbir metin temizliği
veya tahmini (uydurma) alan eklemeden ham sayfa/HTTP bilgilerini içeren
metadata JSON kayıtlarını indirir.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import shutil
import sys
import time
import traceback
from typing import Optional, Dict, Any, List, Tuple
import urllib.error
import urllib.parse
import urllib.request
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By


def slug_baslik_coz(url: str) -> str:
    if not url:
        return ""
    path = urllib.parse.urlparse(url).path.strip("/")
    if not path:
        return ""
    son_parca = urllib.parse.unquote(path.split("/")[-1])
    son_parca = re.sub(r"^\d+-", "", son_parca)
    return son_parca


def dosya_adi_etiketi_temizle(metin: str, maks_uzunluk: int = 55) -> str:
    if not metin:
        return ""
    temiz = str(metin).strip()
    temiz = re.sub(r'[\\/*?:"<>|\[\]\r\n\t]+', "", temiz)
    temiz = re.sub(r"[\s\-]+", "_", temiz).strip("._")
    if len(temiz) > maks_uzunluk:
        temiz = temiz[:maks_uzunluk].rstrip("._")
    return temiz


def deterministik_dosya_adi_olustur(orijinal_dosya_adi: str, pdf_url: str, pdf_kayit: Dict[str, Any]) -> str:
    kok, uzanti = os.path.splitext(orijinal_dosya_adi)
    if not uzanti or uzanti.lower() != ".pdf":
        uzanti = ".pdf"

    kurum_raw = (pdf_kayit.get("kaynak_domain") or "").replace("www.", "")
    kurum_etiket = dosya_adi_etiketi_temizle(kurum_raw, maks_uzunluk=30)

    kategori_raw = pdf_kayit.get("kategori_adi") or "Rapor"
    kategori_etiket = dosya_adi_etiketi_temizle(kategori_raw, maks_uzunluk=45)

    url_hash = hashlib.sha256(pdf_url.encode("utf-8", errors="replace")).hexdigest()[:8]

    etiket_parcalari = [p for p in (kurum_etiket, kategori_etiket, url_hash) if p]
    kategori_eki = "__[" + "__".join(etiket_parcalari) + "]"

    temiz_kok = dosya_adi_etiketi_temizle(kok, maks_uzunluk=70) or "belge"
    return f"{temiz_kok}{kategori_eki}{uzanti}"


def ham_sayfa_verisini_cikar(driver) -> Tuple[Dict[str, Any], str]:
    raw_html = driver.page_source or ""
    sayfa_title = driver.title

    meta_etiketleri: List[Dict[str, Optional[str]]] = []
    try:
        metas = driver.find_elements(By.TAG_NAME, "meta")
        for m in metas:
            attrs = {}
            for attr_name in ("name", "property", "http-equiv", "content", "charset"):
                val = m.get_attribute(attr_name)
                if val is not None:
                    attrs[attr_name] = val
            if attrs:
                meta_etiketleri.append(attrs)
    except Exception:
        pass

    time_etiketleri: List[Dict[str, Optional[str]]] = []
    try:
        time_elems = driver.find_elements(By.TAG_NAME, "time")
        for t_el in time_elems:
            time_etiketleri.append({
                "datetime": t_el.get_attribute("datetime"),
                "text": t_el.text
            })
    except Exception:
        pass

    baslik_etiketleri: Dict[str, List[str]] = {"h1": [], "h2": [], "h3": []}
    for tag in ("h1", "h2", "h3"):
        try:
            elems = driver.find_elements(By.TAG_NAME, tag)
            for el in elems:
                txt = el.text
                if txt:
                    baslik_etiketleri[tag].append(txt)
        except Exception:
            pass

    return {
        "document_title": sayfa_title,
        "html_meta_tags": meta_etiketleri,
        "html_time_elements": time_etiketleri,
        "html_headings": baslik_etiketleri,
    }, raw_html


def pdf_baglantisi_mi(d_link, resolved_url: str) -> bool:
    if not resolved_url:
        return False

    parsed = urllib.parse.urlparse(resolved_url)
    if parsed.scheme not in ("http", "https"):
        return False

    path_lower = urllib.parse.unquote(parsed.path or "").lower()
    query_lower = urllib.parse.unquote(parsed.query or "").lower()

    if path_lower.endswith(".pdf"):
        return True

    if ".pdf" in query_lower:
        return True

    try:
        type_attr = (d_link.get_attribute("type") or "").strip().lower()
        if type_attr == "application/pdf":
            return True

        download_attr = d_link.get_attribute("download")
        if download_attr is not None:
            dl_lower = download_attr.strip().lower()
            if dl_lower.endswith(".pdf") or "/download/" in path_lower or "/files/" in path_lower or "/uploads/" in path_lower:
                return True
    except Exception:
        pass

    return False


def _load_json_safe(file_path: str, default_factory) -> Any:
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as je:
            backup = f"{file_path}.corrupt.{int(time.time())}"
            print(f"[KRİTİK] {file_path} bozuk ({je}), yedekleniyor: {backup}")
            try:
                shutil.copy2(file_path, backup)
            except Exception:
                pass
        except Exception as e:
            print(f"[HATA] {file_path} okunamadı: {e}")
    return default_factory()


def _save_json_atomic(file_path: str, data: Any):
    try:
        tmp_path = f"{file_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, file_path)
    except Exception as e:
        print(f"[HATA] {file_path} kaydedilemedi: {e}")


class HataKaydedici:
    def __init__(self, cikti_klasoru: str):
        self.hata_dosyasi = os.path.join(cikti_klasoru, "hata.json")

    def _oku(self) -> Dict[str, Any]:
        varsayilan = lambda: {
            "kategori_hatalari": {},
            "detay_sayfasi_hatalari": {},
            "indirme_hatalari": {},
            "kritik_hatalar": []
        }
        veri = _load_json_safe(self.hata_dosyasi, varsayilan)
        if not isinstance(veri, dict):
            return varsayilan()
        for k, v in varsayilan().items():
            if k not in veri:
                veri[k] = v
        return veri

    def _yaz(self, veri: Dict[str, Any]):
        _save_json_atomic(self.hata_dosyasi, veri)

    def kategori_hatasi_ekle(self, url: str, sayfa_no: int, hata_mesaji: str, kat_bilgi: Optional[Dict[str, Any]] = None):
        veri = self._oku()
        key = f"{url}#page={sayfa_no}"
        mevcut = veri["kategori_hatalari"].get(key, {})
        veri["kategori_hatalari"][key] = {
            "url": url,
            "sayfa_no": sayfa_no,
            "kategori_bilgisi": kat_bilgi or mevcut.get("kategori_bilgisi"),
            "hata_nedeni": hata_mesaji,
            "deneme_sayisi": mevcut.get("deneme_sayisi", 0) + 1,
            "son_deneme_utc": datetime.now(timezone.utc).isoformat()
        }
        self._yaz(veri)

    def kategori_hatasi_sil(self, url: str, sayfa_no: int):
        if not os.path.exists(self.hata_dosyasi):
            return
        veri = self._oku()
        key = f"{url}#page={sayfa_no}"
        if key in veri["kategori_hatalari"]:
            del veri["kategori_hatalari"][key]
            self._yaz(veri)

    def detay_hatasi_ekle(self, rapor_url: str, hata_mesaji: str, kaynak_meta: Optional[Dict[str, Any]] = None):
        veri = self._oku()
        mevcut = veri["detay_sayfasi_hatalari"].get(rapor_url, {})
        veri["detay_sayfasi_hatalari"][rapor_url] = {
            "rapor_detay_sayfasi_url": rapor_url,
            "kaynak_meta": kaynak_meta or mevcut.get("kaynak_meta"),
            "hata_nedeni": hata_mesaji,
            "deneme_sayisi": mevcut.get("deneme_sayisi", 0) + 1,
            "son_deneme_utc": datetime.now(timezone.utc).isoformat()
        }
        self._yaz(veri)

    def detay_hatasi_sil(self, rapor_url: str):
        if not os.path.exists(self.hata_dosyasi):
            return
        veri = self._oku()
        if rapor_url in veri["detay_sayfasi_hatalari"]:
            del veri["detay_sayfasi_hatalari"][rapor_url]
            self._yaz(veri)

    def indirme_hatasi_ekle(
        self,
        pdf_url: str,
        hata_mesaji: str,
        http_kodu: Optional[int],
        pdf_kayit: Dict[str, Any],
        kalici_hata: bool = False
    ):
        veri = self._oku()
        mevcut = veri["indirme_hatalari"].get(pdf_url, {})
        veri["indirme_hatalari"][pdf_url] = {
            "pdf_indirme_url": pdf_url,
            "hata_nedeni": hata_mesaji,
            "http_kodu": http_kodu,
            "kalici_hata": kalici_hata or (http_kodu == 404),
            "pdf_kayit": pdf_kayit,
            "deneme_sayisi": mevcut.get("deneme_sayisi", 0) + 1,
            "son_deneme_utc": datetime.now(timezone.utc).isoformat()
        }
        self._yaz(veri)

    def indirme_hatasi_sil(self, pdf_url: str):
        if not os.path.exists(self.hata_dosyasi):
            return
        veri = self._oku()
        if pdf_url in veri["indirme_hatalari"]:
            del veri["indirme_hatalari"][pdf_url]
            self._yaz(veri)

    def kritik_hata_ekle(self, hata_mesaji: str, iz_kaydi: str):
        veri = self._oku()
        veri["kritik_hatalar"].append({
            "hata_nedeni": hata_mesaji,
            "traceback": iz_kaydi,
            "zaman_utc": datetime.now(timezone.utc).isoformat()
        })
        self._yaz(veri)


def katalog_guncelle(cikti_klasoru: str, yeni_metadatalar: List[Dict[str, Any]], taranan_urller: List[str]):
    katalog_yolu = os.path.join(cikti_klasoru, "indirilen_dosyalar_metadata.json")
    mevcut_katalog = _load_json_safe(katalog_yolu, lambda: {})
    if not isinstance(mevcut_katalog, dict):
        mevcut_katalog = {}

    dosyalar_by_key: Dict[str, Dict[str, Any]] = {}

    for item in mevcut_katalog.get("dosyalar", []):
        if isinstance(item, dict):
            k = item.get("nereden_geldi", {}).get("pdf_indirme_url") or item.get("dosya_adi")
            if k:
                dosyalar_by_key[k] = item

    if os.path.exists(cikti_klasoru):
        for fname in os.listdir(cikti_klasoru):
            if fname.endswith(".metadata.json") and fname != "indirilen_dosyalar_metadata.json":
                m_path = os.path.join(cikti_klasoru, fname)
                try:
                    with open(m_path, "r", encoding="utf-8") as mf:
                        m_obj = json.load(mf)
                    if isinstance(m_obj, dict):
                        k = m_obj.get("nereden_geldi", {}).get("pdf_indirme_url") or m_obj.get("dosya_adi")
                        if k:
                            dosyalar_by_key[k] = m_obj
                except Exception:
                    pass

    for item in yeni_metadatalar:
        if isinstance(item, dict):
            k = item.get("nereden_geldi", {}).get("pdf_indirme_url") or item.get("dosya_adi")
            if k:
                dosyalar_by_key[k] = item

    onceki_urller = mevcut_katalog.get("taranan_ana_urller", [])
    birlesik_urller = list(dict.fromkeys((onceki_urller if isinstance(onceki_urller, list) else []) + list(taranan_urller)))

    tum_liste = list(dosyalar_by_key.values())
    guncel_katalog = {
        "toplam_indirilen_dosya": len(tum_liste),
        "son_guncelleme_zamani_utc": datetime.now(timezone.utc).isoformat(),
        "taranan_ana_urller": birlesik_urller,
        "dosyalar": tum_liste,
    }
    _save_json_atomic(katalog_yolu, guncel_katalog)
    return katalog_yolu, len(tum_liste)


def tek_pdf_indir_ve_kaydet(
    pdf_kayit: Dict[str, Any],
    cikti_klasoru: str,
    hata_kaydedici: HataKaydedici,
    headers: Dict[str, str],
    sira_bilgisi: str = ""
) -> Optional[Dict[str, Any]]:
    pdf_url = pdf_kayit["pdf_indirme_url"]
    raw_filename = urllib.parse.unquote(os.path.basename(urllib.parse.urlparse(pdf_url).path))
    raw_filename = re.sub(r'[\\/*?:"<>|\r\n\t]+', "_", raw_filename).strip()

    if not raw_filename or not raw_filename.lower().endswith(".pdf"):
        aday_isim = slug_baslik_coz(pdf_kayit.get("rapor_detay_sayfasi_url", ""))
        aday_isim = re.sub(r'[\\/*?:"<>|\s]+', "_", aday_isim).strip("_")
        raw_filename = f"{aday_isim or 'rapor'}.pdf"

    orijinal_dosya_adi = raw_filename
    filename = deterministik_dosya_adi_olustur(orijinal_dosya_adi, pdf_url, pdf_kayit)

    dosya_yolu = os.path.join(cikti_klasoru, filename)
    gecici_dosya_yolu = f"{dosya_yolu}.part"
    metadata_dosya_adi = f"{filename}.metadata.json"
    metadata_dosya_yolu = os.path.join(cikti_klasoru, metadata_dosya_adi)

    detay_html_icerik = pdf_kayit.get("_detay_raw_html")
    detay_html_dosya_adi = f"{filename}.source.html"
    detay_html_dosya_yolu = os.path.join(cikti_klasoru, detay_html_dosya_adi)

    goreli_dosya_yolu = os.path.relpath(dosya_yolu, cikti_klasoru).replace("\\", "/")

    if os.path.exists(dosya_yolu) and os.path.exists(metadata_dosya_yolu):
        try:
            with open(dosya_yolu, "rb") as pf:
                ilk_baytlar = pf.read(1024)
            if b"%PDF-" in ilk_baytlar:
                with open(metadata_dosya_yolu, "r", encoding="utf-8") as mf:
                    mevcut_meta = json.load(mf)
                hata_kaydedici.indirme_hatasi_sil(pdf_url)
                print(f"  {sira_bilgisi} Zaten mevcut ve geçerli PDF, atlanıyor: {filename}")
                return mevcut_meta
        except Exception:
            pass

    max_deneme = 3
    son_hata_mesaji = ""
    son_http_kodu: Optional[int] = None
    kalici_mi = False

    for deneme in range(1, max_deneme + 1):
        print(f"  {sira_bilgisi} İndiriliyor: {filename} (Deneme {deneme}/{max_deneme})")
        try:
            if os.path.exists(gecici_dosya_yolu):
                try:
                    os.remove(gecici_dosya_yolu)
                except Exception:
                    pass

            pdf_req = urllib.request.Request(pdf_url, headers=headers)
            sha256_hash = hashlib.sha256()
            toplam_boyut = 0
            ilk_parca = b""

            with urllib.request.urlopen(pdf_req, timeout=60) as response:
                son_http_kodu = getattr(response, "status", None) or response.getcode()
                http_headers = dict(response.info())
                content_type = http_headers.get("Content-Type") or http_headers.get("content-type")
                last_modified = http_headers.get("Last-Modified") or http_headers.get("last-modified")
                content_disposition = http_headers.get("Content-Disposition") or http_headers.get("content-disposition")

                with open(gecici_dosya_yolu, "wb") as f:
                    while True:
                        chunk = response.read(1024 * 64)
                        if not chunk:
                            break
                        if len(ilk_parca) < 1024:
                            ilk_parca += chunk[: 1024 - len(ilk_parca)]
                        f.write(chunk)
                        sha256_hash.update(chunk)
                        toplam_boyut += len(chunk)

            if b"%PDF-" not in ilk_parca:
                try:
                    os.remove(gecici_dosya_yolu)
                except Exception:
                    pass
                onizleme = ilk_parca[:200].decode("utf-8", errors="replace")
                raise ValueError(
                    f"İndirilen içerik geçerli bir PDF değil (%PDF- imzası bulunamadı, Content-Type: {content_type}). "
                    f"İlk baytlar: {onizleme!r}"
                )

            os.replace(gecici_dosya_yolu, dosya_yolu)

            kaydedilen_html_dosyasi = None
            if detay_html_icerik:
                try:
                    with open(detay_html_dosya_yolu, "w", encoding="utf-8") as hf:
                        hf.write(detay_html_icerik)
                    kaydedilen_html_dosyasi = os.path.relpath(detay_html_dosya_yolu, cikti_klasoru).replace("\\", "/")
                except Exception as he:
                    print(f"    [UYARI] Detay HTML kaynağı yazılamadı: {he}")
            elif os.path.exists(detay_html_dosya_yolu):
                kaydedilen_html_dosyasi = os.path.relpath(detay_html_dosya_yolu, cikti_klasoru).replace("\\", "/")

            temiz_kayit = {k: v for k, v in pdf_kayit.items() if k != "_detay_raw_html"}

            tam_metadata = {
                "dosya_adi": filename,
                "orijinal_dosya_adi": orijinal_dosya_adi,
                "dosya_yolu": goreli_dosya_yolu,
                "detay_sayfasi_ham_html_dosyasi": kaydedilen_html_dosyasi,
                "mime_type": content_type,
                "http_status_code": son_http_kodu,
                "http_response_headers": http_headers,
                "dosya_boyutu_byte": toplam_boyut,
                "sha256": sha256_hash.hexdigest(),
                "sunucu_son_degisiklik_tarihi": last_modified,
                "content_disposition": content_disposition,
                "indirme_zamani_utc": datetime.now(timezone.utc).isoformat(),
                "nereden_geldi": {
                    "kurum_domain": temiz_kayit.get("kaynak_domain"),
                    "ana_giris_url": temiz_kayit.get("ana_giris_url"),
                    "kategori_url": temiz_kayit.get("kategori_url"),
                    "kategori_adi": temiz_kayit.get("kategori_adi"),
                    "bulundugu_liste_sayfasi_url": temiz_kayit.get("bulundugu_liste_sayfasi_url"),
                    "bulundugu_sayfa_no": temiz_kayit.get("bulundugu_sayfa_no"),
                    "rapor_detay_sayfasi_url": temiz_kayit.get("rapor_detay_sayfasi_url"),
                    "pdf_indirme_url": pdf_url,
                },
                "sayfa_ham_verisi": {
                    **(temiz_kayit.get("belge_bilgisi") or {}),
                    "liste_link_metni": temiz_kayit.get("liste_link_metni"),
                    "liste_link_title_attr": temiz_kayit.get("liste_link_title_attr"),
                    "indirme_link_metni": temiz_kayit.get("indirme_link_metni"),
                    "indirme_link_title_attr": temiz_kayit.get("indirme_link_title_attr"),
                },
            }

            _save_json_atomic(metadata_dosya_yolu, tam_metadata)
            hata_kaydedici.indirme_hatasi_sil(pdf_url)
            print(f"    -> Kaydedildi: {filename} (+ {metadata_dosya_adi})")
            return tam_metadata

        except urllib.error.HTTPError as he:
            son_http_kodu = he.code
            son_hata_mesaji = f"HTTPError {he.code}: {he.reason}"
            if os.path.exists(gecici_dosya_yolu):
                try:
                    os.remove(gecici_dosya_yolu)
                except Exception:
                    pass
            print(f"    -> HTTP Hatası ({pdf_url}): {son_hata_mesaji}")
            if he.code == 404:
                kalici_mi = True
                break
            if deneme < max_deneme:
                time.sleep(3 * deneme)
        except Exception as e:
            son_hata_mesaji = f"{type(e).__name__}: {e}"
            if os.path.exists(gecici_dosya_yolu):
                try:
                    os.remove(gecici_dosya_yolu)
                except Exception:
                    pass
            print(f"    -> Hata oluştu ({pdf_url}): {son_hata_mesaji}")
            if deneme < max_deneme:
                time.sleep(3 * deneme)

    temiz_kayit = {k: v for k, v in pdf_kayit.items() if k != "_detay_raw_html"}
    print(f"    -> [HATA KAYDI] {filename} ({pdf_url}) indirilemedi, hata.json'a kaydediliyor.")
    hata_kaydedici.indirme_hatasi_ekle(
        pdf_url=pdf_url,
        hata_mesaji=son_hata_mesaji or "Bilinmeyen indirme hatası",
        http_kodu=son_http_kodu,
        pdf_kayit=temiz_kayit,
        kalici_hata=kalici_mi
    )
    return None


def raporlari_tara_ve_indir(
    url_listesi: List[str],
    toplam_sayfa: Optional[int],
    cikti_klasoru: str,
    kategori_kalibi: str = "/reports/category/",
    detay_kalibi: str = "/reports/"
):
    if not os.path.exists(cikti_klasoru):
        os.makedirs(cikti_klasoru, exist_ok=True)
        print(f"[*] Çıktı klasörü oluşturuldu: {cikti_klasoru}")

    hata_kaydedici = HataKaydedici(cikti_klasoru)
    tum_metadatalar: List[Dict[str, Any]] = []

    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument(
        "User-Agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    driver = webdriver.Chrome(options=chrome_options)
    taranacak_kategoriler: Dict[str, Dict[str, Any]] = {}

    try:
        for ana_url in url_listesi:
            if not ana_url.startswith("http://") and not ana_url.startswith("https://"):
                ana_url = "https://" + ana_url

            print(f"\n[*] Ana sayfa/kategori inceleniyor: {ana_url}")
            try:
                driver.get(ana_url)
                time.sleep(4)
            except Exception as e:
                err_msg = f"{type(e).__name__}: {e}"
                print(f"  [-] Ana kategori açılamadı ({ana_url}): {err_msg}")
                hata_kaydedici.kategori_hatasi_ekle(ana_url, 1, err_msg)
                continue

            ana_baslik = driver.title or slug_baslik_coz(ana_url)
            taranacak_kategoriler[ana_url] = {
                "ana_kaynak_url": ana_url,
                "kategori_url": ana_url,
                "kategori_adi": slug_baslik_coz(ana_url) or ana_baslik,
            }

            links = driver.find_elements(By.TAG_NAME, "a")
            for link in links:
                try:
                    href = link.get_attribute("href")
                    if href and kategori_kalibi and kategori_kalibi in href:
                        alt_kategori_url = urllib.parse.urljoin(ana_url, href)
                        link_metni = link.text or slug_baslik_coz(alt_kategori_url)
                        if alt_kategori_url not in taranacak_kategoriler:
                            taranacak_kategoriler[alt_kategori_url] = {
                                "ana_kaynak_url": ana_url,
                                "kategori_url": alt_kategori_url,
                                "kategori_adi": link_metni,
                            }
                except Exception:
                    pass

        print(
            f"\n[*] Toplam {len(taranacak_kategoriler)} adet kategori (ana + alt) "
            "belirlendi. Sayfalar taranıyor..."
        )

        rapor_kayitlari: Dict[str, Dict[str, Any]] = {}
        dogrudan_pdf_kayitlari: List[Dict[str, Any]] = []
        gorulen_pdf_urls = set()

        for kat_url, kat_bilgi in taranacak_kategoriler.items():
            sayfa = 1
            kategori_gorulen_urls = set()
            while True:
                if toplam_sayfa is not None and toplam_sayfa > 0 and sayfa > toplam_sayfa:
                    break

                parsed_url = urllib.parse.urlparse(kat_url)
                query_params = urllib.parse.parse_qs(parsed_url.query)
                if sayfa > 1 or "page" in query_params:
                    query_params["page"] = [str(sayfa)]

                new_query = urllib.parse.urlencode(query_params, doseq=True)
                current_url = urllib.parse.urlunparse((
                    parsed_url.scheme,
                    parsed_url.netloc,
                    parsed_url.path,
                    parsed_url.params,
                    new_query,
                    parsed_url.fragment,
                ))

                try:
                    driver.get(current_url)
                    time.sleep(3)

                    sayfa_meta, sayfa_raw_html = ham_sayfa_verisini_cikar(driver)
                    links = driver.find_elements(By.TAG_NAME, "a")
                    bulunan_yeni = 0
                    kategori_ici_yeni = 0

                    for link in links:
                        try:
                            href = link.get_attribute("href")
                        except Exception:
                            continue
                        if not href:
                            continue

                        tam_url = urllib.parse.urljoin(current_url, href)

                        if pdf_baglantisi_mi(link, tam_url):
                            if tam_url not in kategori_gorulen_urls:
                                kategori_gorulen_urls.add(tam_url)
                                kategori_ici_yeni += 1
                            if tam_url not in gorulen_pdf_urls:
                                gorulen_pdf_urls.add(tam_url)
                                dogrudan_pdf_kayitlari.append({
                                    "kaynak_domain": parsed_url.netloc,
                                    "ana_giris_url": kat_bilgi["ana_kaynak_url"],
                                    "kategori_url": kat_url,
                                    "kategori_adi": kat_bilgi["kategori_adi"] or slug_baslik_coz(kat_url),
                                    "bulundugu_liste_sayfasi_url": current_url,
                                    "bulundugu_sayfa_no": sayfa,
                                    "rapor_detay_sayfasi_url": current_url,
                                    "liste_link_metni": link.text,
                                    "liste_link_title_attr": link.get_attribute("title"),
                                    "pdf_indirme_url": tam_url,
                                    "indirme_link_metni": link.text,
                                    "indirme_link_title_attr": link.get_attribute("title"),
                                    "belge_bilgisi": sayfa_meta,
                                    "_detay_raw_html": sayfa_raw_html,
                                })
                                bulunan_yeni += 1
                            continue

                        if detay_kalibi and detay_kalibi in href:
                            if kategori_kalibi and kategori_kalibi in href:
                                continue
                            if tam_url not in kategori_gorulen_urls:
                                kategori_gorulen_urls.add(tam_url)
                                kategori_ici_yeni += 1
                            if tam_url not in rapor_kayitlari:
                                rapor_kayitlari[tam_url] = {
                                    "kaynak_domain": parsed_url.netloc,
                                    "ana_giris_url": kat_bilgi["ana_kaynak_url"],
                                    "kategori_url": kat_url,
                                    "kategori_adi": kat_bilgi["kategori_adi"] or slug_baslik_coz(kat_url),
                                    "bulundugu_liste_sayfasi_url": current_url,
                                    "bulundugu_sayfa_no": sayfa,
                                    "rapor_detay_sayfasi_url": tam_url,
                                    "liste_link_metni": link.text,
                                    "liste_link_title_attr": link.get_attribute("title"),
                                }
                                bulunan_yeni += 1

                    hata_kaydedici.kategori_hatasi_sil(kat_url, sayfa)

                    if kategori_ici_yeni > 0:
                        print(
                            f"  [+] [Sayfa {sayfa}] {kat_url} -> "
                            f"{bulunan_yeni} yeni kayıt ({kategori_ici_yeni} kategori içi bağlantı) bulundu."
                        )
                        sayfa += 1
                    else:
                        break
                except Exception as e:
                    err_msg = f"{type(e).__name__}: {e}"
                    print(f"  [-] Kategori sayfası taranamadı ({current_url}): {err_msg}")
                    hata_kaydedici.kategori_hatasi_ekle(kat_url, sayfa, err_msg, kat_bilgi=kat_bilgi)
                    break

        print(
            f"\n[*] Toplam {len(rapor_kayitlari)} adet rapor detay sayfası ve "
            f"{len(dogrudan_pdf_kayitlari)} adet doğrudan PDF bağlantısı bulundu."
        )

        gercek_pdf_kayitlari: List[Dict[str, Any]] = list(dogrudan_pdf_kayitlari)

        for i, (rapor_url, kaynak_meta) in enumerate(rapor_kayitlari.items()):
            print(f"  [{i+1}/{len(rapor_kayitlari)}] Detay sayfası inceleniyor: {rapor_url}")
            try:
                driver.get(rapor_url)
                time.sleep(2)

                sayfa_meta, raw_html = ham_sayfa_verisini_cikar(driver)
                detay_links = driver.find_elements(By.TAG_NAME, "a")
                bulunan_pdf_sayisi = 0

                for d_link in detay_links:
                    try:
                        d_href = d_link.get_attribute("href")
                    except Exception:
                        continue
                    if not d_href:
                        continue

                    pdf_url = urllib.parse.urljoin(rapor_url, d_href)
                    if pdf_baglantisi_mi(d_link, pdf_url):
                        if pdf_url not in gorulen_pdf_urls:
                            gorulen_pdf_urls.add(pdf_url)
                            kayit = {
                                **kaynak_meta,
                                "pdf_indirme_url": pdf_url,
                                "indirme_link_metni": d_link.text,
                                "indirme_link_title_attr": d_link.get_attribute("title"),
                                "belge_bilgisi": sayfa_meta,
                                "_detay_raw_html": raw_html,
                            }
                            gercek_pdf_kayitlari.append(kayit)
                            bulunan_pdf_sayisi += 1

                hata_kaydedici.detay_hatasi_sil(rapor_url)
                if bulunan_pdf_sayisi == 0:
                    uyari_msg = "Detay sayfasında geçerli bir PDF bağlantısı bulunamadı."
                    print(f"    [!] {uyari_msg} ({rapor_url})")
                    hata_kaydedici.detay_hatasi_ekle(rapor_url, uyari_msg, kaynak_meta=kaynak_meta)
            except Exception as e:
                err_msg = f"{type(e).__name__}: {e}"
                print(f"  [-] Detay sayfası okunamadı ({rapor_url}): {err_msg}")
                hata_kaydedici.detay_hatasi_ekle(rapor_url, err_msg, kaynak_meta=kaynak_meta)

        print(f"\n[*] Toplam {len(gercek_pdf_kayitlari)} adet PDF bağlantısı tespit edildi.")
        print("[*] İndirme işlemi başlatılıyor...\n")

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        }

        for i, pdf_kayit in enumerate(gercek_pdf_kayitlari):
            sira_str = f"[{i+1}/{len(gercek_pdf_kayitlari)}]"
            meta = tek_pdf_indir_ve_kaydet(
                pdf_kayit=pdf_kayit,
                cikti_klasoru=cikti_klasoru,
                hata_kaydedici=hata_kaydedici,
                headers=headers,
                sira_bilgisi=sira_str
            )
            if meta:
                tum_metadatalar.append(meta)

    except Exception as e:
        err_msg = f"{type(e).__name__}: {e}"
        tb_str = traceback.format_exc()
        print(f"[HATA] Kritik bir hata meydana geldi: {err_msg}")
        hata_kaydedici.kritik_hata_ekle(err_msg, tb_str)
    finally:
        try:
            driver.quit()
        except Exception:
            pass
        katalog_yolu, toplam_katalog = katalog_guncelle(cikti_klasoru, tum_metadatalar, url_listesi)
        print(
            f"\n[BİLGİ] İşlem sona erdi. Bu çalışmada işlenen: {len(tum_metadatalar)} | "
            f"Katalogdaki toplam dosya: {toplam_katalog}"
        )
        print(f"[BİLGİ] Toplu metadata kataloğu: {katalog_yolu}")
        if os.path.exists(hata_kaydedici.hata_dosyasi):
            print(f"[BİLGİ] Hata kayıtları: {hata_kaydedici.hata_dosyasi}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Sayıştay ve diğer kamu sitelerinden PDF rapor, ham HTML ve metadata indirme aracı.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Örnek kullanım:\n"
            "  python dosya_indirici.py -u https://www.sayistay.gov.tr/reports/category/13-faaliyet-genel-degerlendirme-raporlari -o faaliyetler\n"
            "  python dosya_indirici.py -u url1 url2 -s 5 -o indirilen_raporlar\n"
        ),
    )

    parser.add_argument(
        "-u",
        "--urls",
        nargs="+",
        required=True,
        help="Taranacak sayfa veya kategori adresleri"
    )
    parser.add_argument(
        "-s",
        "--sayfa",
        type=int,
        default=None,
        help="Her kategori için taranacak maksimum sayfa sayısı (varsayılan: sınırsız / tüm sayfalar)"
    )
    parser.add_argument(
        "-o",
        "--cikti",
        default="indirilen_pdfler",
        help="PDF, ham HTML ve metadata dosyalarının kaydedileceği klasör adı"
    )
    parser.add_argument(
        "--category-pattern",
        default="/reports/category/",
        help="Alt kategori linklerini tespit etmek için URL alt dizgesi (varsayılan: /reports/category/)"
    )
    parser.add_argument(
        "--detail-pattern",
        default="/reports/",
        help="Rapor detay sayfalarını tespit etmek için URL alt dizgesi (varsayılan: /reports/)"
    )

    args = parser.parse_args()
    raporlari_tara_ve_indir(
        url_listesi=args.urls,
        toplam_sayfa=args.sayfa,
        cikti_klasoru=args.cikti,
        kategori_kalibi=args.category_pattern,
        detay_kalibi=args.detail_pattern
    )
