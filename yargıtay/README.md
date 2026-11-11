# Yargıtay Karar Arama Otomasyonu ve Arşivleyici

Bu modül, Yargıtay Karar Arama Portalı (`karararama.yargitay.gov.tr`) üzerinde Selenium ve Chromium kullanarak tarih aralıklı arama sorguları yürüten ve kararları tam metinleriyle birlikte yerel diske arşivleyen otomasyon aracıdır.

## Çalışma Mantığı

1. **Tarih Dilimleme (Chunking):**
   Yargıtay arama arayüzü tek seferde listelenebilecek sonuçlara sınır koyduğu için, arama aralığı varsayılan 5'er günlük dilimlere bölünür. Arama işlemi günümüzden geçmişe doğru kademeli olarak yürütülür.

2. **Sayfa İçi JavaScript Otomasyonu:**
   Detaylı arama formundaki tarih girişleri ve buton tıklamaları Selenium script enjeksiyonu (`execute_script`) ile hızlıca gerçekleştirilir. Sayfadaki DataTables nesnesi doğrudan denetlenir.

3. **Çift Katmanlı Durum Takibi (SQLite ve Bellek):**
   - `archive_state.db` SQLite veritabanında `indirilen_kararlar` ve `tamamlanan_araliklar` tabloları tutulur.
   - İndirilen tüm doküman ID'leri RAM üzerinde bir kümede (set) saklanarak $O(1)$ hızında mükerrerlik denetimi yapılır.
   - Program kapatılıp tekrar açıldığında tamamlanmış tarih aralıkları atlanır ve kalınan sıra numarasından devam edilir.

4. **Tarayıcı İçi Asenkron Doküman Çekme:**
   Her kararın tam metni, mevcut oturum çerezleri ve yetkileri korunarak tarayıcı içerisinden `fetch('/getDokuman?id=...')` çağrısıyla çekilir. Böylece oturum düşmesi veya WAF engelleri en aza indirilir.

5. **Hız ve Erişim Sınırı Koruması:**
   Arama veya doküman çekme esnasında erişim sınırlaması veya güvenlik uyarısı tespit edildiğinde, işlem durdurulup artan sürelerle beklenir.

6. **Yıl Bazlı Dizinleme:**
   İndirilen kararlar `yargitayjson/YYYY/karar_{global_id}.json` biçiminde hiyerarşik olarak saklanır.

## Gereksinimler

- Python 3.8+
- Google Chrome veya Chromium
- `selenium`

Kurulum:
```bash
pip install selenium
```

## Kullanım Parametreleri

Betiği komut satırından çeşitli argümanlarla özelleştirerek çalıştırabilirsiniz:

```bash
# Varsayılan ayarlarla (2020'den 2026'ya kadar 5 günlük dilimlerle arka planda) çalıştırma:
python yargitay_indirici.py

# Belirli bir tarih aralığında ve tarayıcıyı ekranda görerek çalıştırma:
python yargitay_indirici.py --start 01.01.2023 --end 31.12.2023 --chunk-days 3 --gui --output yargitay_2023
```

### Parametre Açıklamaları

- `--output`: Kararların kaydedileceği ana dizin adı (varsayılan: `yargitayjson`).
- `--start`: Arama başlangıç tarihi (format: `GG.AA.YYYY`, varsayılan: `01.01.2020`).
- `--end`: Arama bitiş tarihi (format: `GG.AA.YYYY`, varsayılan: `16.10.2026`).
- `--chunk-days`: Her arama adımındaki gün sayısı (varsayılan: 5).
- `--gui`: Başsız (headless) mod yerine tarayıcıyı ekranda görünür açar.
- `--max`: İndirilecek maksimum toplam karar sayısı sınırı.

## Çıktı JSON Formatı

Her karar belgesi şu yapıda kaydedilir:
```json
{
  "global_id": 105,
  "dokuman_id": "987654",
  "daire": "1. Ceza Dairesi",
  "esasNo": "2022/100",
  "kararNo": "2023/250",
  "kararTarihi": "15/02/2023",
  "kayit_yili_klasoru": "2023",
  "indirme_zamani": "2026-11-11 10:15:30",
  "data": { ... }
}
```

[Ana Dokümantasyona Dön](../README.md)
