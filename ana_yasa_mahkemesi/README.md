# Anayasa Mahkemesi Karar İndirici

Bu modül, Anayasa Mahkemesi (AYM) Kararlar Bilgi Bankası genel arama API'sini kullanarak kararları gün bazında tarar ve tam metinleriyle birlikte yerel diske JSON formatında arşivler.

## Çalışma Mantığı

1. **API Tabanlı Doğrudan Erişim:**
   Arama portallarının arayüzleri yerine doğrudan `https://kararlarbilgibankasi.anayasa.gov.tr/api/core/public/search` uç noktasına HTTP POST istekleri gönderilir.

2. **Gün Bazlı Geriye Dönük Tarama:**
   Belirlenen başlangıç tarihinden (varsayılan: günümüz veya son kalınan gün) geriye doğru gün gün sorgu atılır. Her güne ait kararlar sayfalama (pagination) ile eksiksiz toplanır.

3. **HTML ve Karakter Temizliği:**
   Gelen karar metinlerindeki HTML etiketleri (`<p>`, `<div>`, `<span>` vb.) ve özel HTML karakter kodları (`&nbsp;`, `&uuml;` vb.) ayıklanır; düzgün biçimlendirilmiş metin elde edilir.

4. **Hiyerarşik Dizinleme:**
   İndirilen kararlar `aymjson/YYYY/AA/GG/` dizin yapısı altında karar veya başvuru numarasına göre adlandırılarak (`karar_{id}.json`) saklanır.

5. **Hata ve Hız Yönetimi (Rate Limiting):**
   Sunucunun 429 (Too Many Requests) yanıtı dönmesi durumunda artan bekleme süreleriyle (exponential backoff) otomatik bekleme yapılır ve oturum yeniden denenir.

6. **Kaldığı Yerden Devam Edebilme:**
   İşlenen son tarih ve önbellek bilgileri `ilerleme_durumu.json` dosyasında tutulur. Program yeniden başlatıldığında kaldığı günden işleme devam eder.

## Gereksinimler

- Python 3.8+
- `requests`
- `urllib3`

Kurulum:
```bash
pip install requests urllib3
```

## Kullanım

Betiği doğrudan çalıştırabilirsiniz:
```bash
python aym_indirici.py
```

Varsayılan olarak çalıştırıldığı dizinde `aymjson` adında bir klasör oluşturur ve arşivlemeye başlar.

## Çıktı JSON Formatı

Her karar dosyası aşağıdaki yapıdadır:
```json
{
    "baslik": "Başvuru veya Karar Başlığı",
    "detay_bilgi": "2020/1234 | Bireysel Başvuru | Karar Tarihi: 2024-05-10",
    "id": "12345",
    "karar_tipi": "BireyselBasvuru",
    "kategori": "Birinci Bölüm",
    "link": "/kbb/pages/search/Tumu?id=12345&type=BireyselBasvuru",
    "onizleme_metni": "Karar konusu özeti",
    "api_detay_icerigi": { ... },
    "meta_veri": {
        "arsivleme_tarihi_utc": "2026-11-11T10:00:00.000000+00:00",
        "karar_tarihi": "2024-05-10",
        "yil": "2024",
        "ay": "05",
        "gun": "10",
        "kayit_dizini": "aymjson/2024/05/10",
        "kaynak": "Anayasa Mahkemesi Kararlar Bilgi Bankası"
    }
}
```

[Ana Dokümantasyona Dön](../README.md)
