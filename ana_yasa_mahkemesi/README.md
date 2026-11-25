# Anayasa Mahkemesi Karar İndirici

Bu modül, Anayasa Mahkemesi (AYM) Kararlar Bilgi Bankası genel arama API'sini kullanarak kararları gün bazında tarar ve API'den elde edilen veriyi hiçbir karakter temizliği veya yapısal değişiklik yapmadan en saf haliyle doğrudan yerel diske JSON formatında arşivler.

## Dosyalar

- **`aym_indirici.py`**: Belirlenen tarih aralığında (varsayılan: günümüzden `1962-01-01`'e kadar) geriye doğru tüm günleri tarayarak kararları indirir.
- **`aym_hatali_indirici.py`**: `ilerleme_durumu.json` içerisinde biriken `hatali_idler` listesindeki indirilememiş/hata vermiş kararları tekrar sorgular; indirilenleri ilgili `Yıl/Ay/Gün` klasörüne kaydedip `hatali_idler` listesinden çıkararak `basarili_idler` listesine taşır.

## Çalışma Mantığı

1. **API Tabanlı Doğrudan Erişim ve Saf JSON Kaydı:**
   Doğrudan `https://kararlarbilgibankasi.anayasa.gov.tr/api/core/public/search` uç noktasına HTTP POST istekleri gönderilir. HTML temizleme, karakter sayısına göre eleme veya sarmalayıcı (`api_detay_icerigi`, `meta_veri` vb.) ekleme yapılmaz; API yanıtı hiçbir müdahale edilmeden en saf haliyle kaydedilir.

2. **Gün Bazlı Geriye Dönük Tarama:**
   Belirlenen başlangıç tarihinden (varsayılan: günümüz veya son kalınan gün) geriye doğru gün gün sorgu atılır. Her güne ait kararlar sayfalama (pagination) ile eksiksiz toplanır.

3. **Hiyerarşik Dizinleme:**
   İndirilen kararlar `aymjson/YYYY/AA/GG/` dizin yapısı altında kararın API'deki benzersiz kimliğine (`id`) göre adlandırılarak (`karar_{id}.json`) saklanır.

4. **İndirme Listesi ve İlerleme Takibi (`ilerleme_durumu.json`):**
   İşlenen son tarih, önbellekteki toplam dosya sayısı, kusursuz inen kararların ID listesi (`basarili_idler`) ve hata veren/indirilemeyen kararların ID listesi (`hatali_idler`) `aymjson/ilerleme_durumu.json` dosyasında tutulur.

5. **Güvenli Durdurma (`Ctrl+C`):**
   İndirme işlemi sırasında `Ctrl+C` (`KeyboardInterrupt`) yapıldığında RAM'de bulunan güncel ilerleme bilgileri (`son_tarih`, `basarili_idler`, `hatali_idler`) veri kaybı olmadan güvenli bir şekilde `ilerleme_durumu.json` dosyasına kaydedilir.

6. **Hata ve Hız Yönetimi (Rate Limiting):**
   Sunucunun 429 (Too Many Requests) veya 5xx yanıtı dönmesi durumunda artan bekleme süreleriyle (exponential backoff) otomatik bekleme yapılır ve istek yeniden denenir.

## Gereksinimler

- Python 3.8+
- `requests`
- `urllib3`

Kurulum:
```bash
pip install requests urllib3
```

## Kullanım

Ana arşivleyiciyi çalıştırmak için:
```bash
python aym_indirici.py
```

Sadece hata veren / indirilemeyen kararları tekrar indirmek için:
```bash
python aym_hatali_indirici.py
```

## `ilerleme_durumu.json` Formatı

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

## API Veri Yapısı ve Önemli Notlar

- **Genel Kategori / Dava Tipi (`kararTipi`):**
  API karar türünü `kararTipi` alanı içerisinde gönderir (`"BireyselBasvuru"`, `"NormDenetimi"`, `"YuceDivan"` vb.). Sonradan yapay bir `kararTuru` alanı eklenmez, API'den gelen ham `kararTipi` değeri korunur (değer yoksa varsayılan atama yapılmaz, ham haliyle `null` kalır).
- **Norm Denetimi Kararları:**
  Norm denetimi kararlarında karar türü ve dosya sonucu için `"kararTuruDosyaSonucuLabel"` (İptal, Ret vb.), başvuru şekli için ise `"basvuruTuru"` (İptal Davası, İtiraz Yolu vb.) alanları kullanılır.
- **Gizlilik Durumu (`gizlilik`):**
  API yanıtlarında `"gizlilik": true` veya `"gizlilik": false` değeri yer alır. Gizliliğin açık olduğu (`true`) AYM kararlarında isimler ve kişisel bilgiler gizlenmiş/kısaltma kullanılarak yazılmış durumdadır.

[Ana Dokümantasyona Dön](../README.md)
