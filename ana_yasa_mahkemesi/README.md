# Anayasa Mahkemesi Karar İndirici

Anayasa Mahkemesi Kararlar Bilgi Bankası API'sini (`https://kararlarbilgibankasi.anayasa.gov.tr/api/core/public/search`) gün gün tarar ve gelen yanıtı hiçbir değişiklik yapmadan ham JSON olarak diske kaydeder.

## Dosyalar

- `aym_indirici.py`: Belirtilen tarih aralığında (varsayılan: bugünden `1962-01-01` tarihine kadar) geriye doğru tüm günleri tarar.
- `aym_hatali_indirici.py`: `hata.json` ve `archive_state.db` içindeki indirilememiş arama sayfalarını ve kararları tekrar sorgular; inenleri ilgili `Yıl/Ay/Gün` (veya `tarihsiz/`) klasörüne kaydedip `idlist.json`'a ekler.

## Özellikler

- API'den gelen yanıt filtrelenmez, HTML temizliği yapılmaz ve dışarıdan ek alan eklenmez.
- Kararlar `aymjson/YYYY/AA/GG/karar_{id}.json` (tarihsiz ise `aymjson/tarihsiz/karar_{id}.json`) yoluna kaydedilir.
- **SQLite Durum Takibi (`archive_state.db`):** İndirilen kararlar, tamamlanan günler, hatalı kararlar ve hatalı arama sayfaları veritabanında tutulur. Program yeniden başlatıldığında bugünden geriye doğru tararken daha önce tamamlanmış günleri `O(1)` hızla atlar; böylece yeni yayımlanan kararlar ve eksik kalan günler kaçırılmaz.
- **Kompakt İndirme Listesi (`idlist.json`):** İndirilen her kararın ID'si, indirme tarihi/saati ve dosya konumu RAM yormadan ve tüm dosyayı baştan yazmadan (`O(1)` dosya sonu ekleme ile) `{"ID":["YYYY-MM-DD HH:MM:SS","YIL/AY/GUN/karar_ID.json"]}` formatında kaydedilir.
- **Detaylı Hata Kaydı (`hata.json`):** İndirilemeyen kararların arama künyesi (metadata), HTTP durum kodu, exception mesajı, deneme sayısı, kalıcı hata durumu (`404` veya `total: 0`), alınamayan arama sayfaları ve eksik kalan günler `hata.json` dosyasında tutulur.

## Kullanım

```bash
pip install requests urllib3

python aym_indirici.py

python aym_indirici.py --start 2023-01-01 --end 2024-01-01 --output aym_2023 --delay 0.15 --limit 500

python aym_hatali_indirici.py --output aymjson --delay 0.15
```

### Parametreler (`aym_indirici.py`)

| Parametre | Varsayılan | Açıklama |
| :--- | :--- | :--- |
| `--output`, `--dir` | `aymjson` | Kararların ve `ilerleme_durumu.json` dosyasının kaydedileceği klasör |
| `--start` | `1962-01-01` | Arama alt sınır / en eski tarih (`YYYY-MM-DD`) |
| `--end` | Bugün / Son kalınan tarih | Arama başlangıç / en yeni tarih (`YYYY-MM-DD`) |
| `--limit` | Sınırsız | Maksimum indirilecek toplam karar sayısı |
| `--delay` | `0.1` | İstekler arası bekleme süresi (saniye) |
| `--page-size` | `50` | Sayfa başına çekilecek karar sayısı |

### Parametreler (`aym_hatali_indirici.py`)

| Parametre | Varsayılan | Açıklama |
| :--- | :--- | :--- |
| `--output`, `--dir` | `aymjson` | `ilerleme_durumu.json` dosyasının bulunduğu arşiv klasörü |
| `--limit` | Sınırsız | Tek seferde denenecek maksimum hatalı karar sayısı |
| `--delay` | `0.15` | İstekler arası bekleme süresi (saniye) |

## `idlist.json` ve `hata.json` Yapısı

### `idlist.json` (Kompakt İndirilenler Listesi)
```json
{
"2020_1234":["2026-11-26 17:20:05","2024/05/10/karar_2020_1234.json"],
"2021_5678":["2026-11-26 17:20:06","2024/05/10/karar_2021_5678.json"]
}
```

### `hata.json`
```json
{
  "hatali_kararlar": {
    "2019/9999": {
      "id": "2019/9999",
      "karar_tarihi": "2024-05-10",
      "karar_tipi": "BireyselBasvuru",
      "arama_kunyesi": { "id": "2019/9999", "kararTarihi": "2024-05-10" },
      "hata_nedeni": "Sunucu HTTP 503 yanıtı döndü",
      "http_kodu": 503,
      "kalici_hata": false,
      "deneme_sayisi": 2,
      "son_deneme": "2026-11-26T17:21:00"
    }
  },
  "hatali_sayfalar": {},
  "eksik_gunler": {}
}
```

## API Alan Notları

- `kararTipi`: Karar kategorisini belirtir (`"BireyselBasvuru"`, `"NormDenetimi"`, `"YuceDivan"` vb.).
- `kararTuruDosyaSonucuLabel` ve `basvuruTuru`: Norm denetimi kararlarında dosya sonucu (İptal, Ret vb.) ve başvuru türünü tutar.
- `gizlilik`: `true` olduğunda karardaki kişisel bilgiler anonimleştirilmiştir.

## Yasal Uyarı ve Sorumluluk Reddi

Bu yazılım yalnızca eğitim, akademik araştırma ve kişisel arşivleme amaçlarıyla geliştirilmiştir. Anayasa Mahkemesi ile herhangi bir resmi bağı yoktur. Kamuya açık verilerin indirilmesi, saklanması ve işlenmesi süreçlerinde ilgili kurumun kullanım koşullarına, KVKK hükümlerine ve yürürlükteki mevzuata uyulması tamamen kullanıcının sorumluluğundadır. Kurum sunucularına aşırı yük bindirmemek için istek gecikme sürelerine (`--delay`) dikkat edilmelidir. Geliştirici, yazılımın kullanımından doğabilecek hiçbir hukuki, idari, cezai veya teknik sonuçtan sorumlu tutulamaz.
