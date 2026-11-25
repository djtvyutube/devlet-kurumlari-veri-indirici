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

# Günlük taramayı başlatma
python aym_indirici.py

# Hata veren kararları tekrar deneme
python aym_hatali_indirici.py
```

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
