# Anayasa Mahkemesi Karar İndirici

Anayasa Mahkemesi Kararlar Bilgi Bankası API'sini (`https://kararlarbilgibankasi.anayasa.gov.tr/api/core/public/search`) gün gün tarar ve gelen yanıtı hiçbir değişiklik yapmadan ham JSON olarak diske kaydeder.

## Dosyalar

- `aym_indirici.py`: Belirtilen tarih aralığında (varsayılan: bugünden `1962-01-01` tarihine kadar) geriye doğru tüm günleri tarar.
- `aym_hatali_indirici.py`: `ilerleme_durumu.json` içindeki `hatali_idler` listesinde biriken indirilememiş kararları tekrar sorgular; inenleri ilgili `Yıl/Ay/Gün` klasörüne kaydedip `basarili_idler` listesine taşır.

## Özellikler

- API'den gelen yanıt filtrelenmez, HTML temizliği yapılmaz ve dışarıdan ek alan eklenmez.
- Kararlar `aymjson/YYYY/AA/GG/karar_{id}.json` yoluna kaydedilir.
- Kalınan tarih, başarılı ve hatalı karar ID'leri `aymjson/ilerleme_durumu.json` dosyasında tutulur. `Ctrl+C` ile durdurulduğunda mevcut durum kaydedilir.

## Kullanım

```bash
pip install requests urllib3

# Varsayılan ayarlarla günlük taramayı başlatma (Bugünden 1962-01-01'e kadar)
python aym_indirici.py

# Belirli tarih aralığı, klasör ve limit belirterek çalıştırma
python aym_indirici.py --start 2023-01-01 --end 2024-01-01 --output aym_2023 --delay 0.15 --limit 500

# Hata veren kararları tekrar deneme
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

## `ilerleme_durumu.json` Yapısı

```json
{
    "son_tarih": "2024-05-10",
    "toplam_onbellek_dosya": 1520,
    "basarili_id_sayisi": 1520,
    "basarili_idler": [
        "2020/1234",
        "2021/5678"
    ],
    "hatali_id_sayisi": 1,
    "hatali_idler": [
        "2019/9999"
    ],
    "guncelleme_zamani_utc": "2026-11-25T07:30:00.000000+00:00"
}
```

## API Alan Notları

- `kararTipi`: Karar kategorisini belirtir (`"BireyselBasvuru"`, `"NormDenetimi"`, `"YuceDivan"` vb.).
- `kararTuruDosyaSonucuLabel` ve `basvuruTuru`: Norm denetimi kararlarında dosya sonucu (İptal, Ret vb.) ve başvuru türünü tutar.
- `gizlilik`: `true` olduğunda karardaki kişisel bilgiler anonimleştirilmiştir.
