# Danıştay Karar İndirici ve Arşivleyici 2.0 (Bedesten / UYAP API)

Bu modül, Danıştay Karar Arama Portalı'nın (`karararama.danistay.gov.tr`) kullandığı yeni **UYAP / Bedesten REST API** altyapısını (`bedesten.adalet.gov.tr`) doğrudan kullanarak kararları günümüzden geçmişe doğru gün gün tarayan, her kararın arama künyesini ve tam metin detay yanıtını SQLite durum takibiyle yerel diske arşivleyen veri toplama aracıdır.

## Çalışma Mantığı

1. **Bedesten / UYAP API 2.0 Doğrudan Erişim:**
   - **Günlük Arama (`getEmsalKarar`):** `https://bedesten.adalet.gov.tr/emsal-karar/getEmsalKarar` uç noktasına `itemTypeList: ["DANISTAYKARAR"]` parametresi ve Türkiye saat dilimine (UTC+3) uyarlanmış günlük UTC başlangıç/bitiş zaman damgaları (`YYYY-MM-DDT21:00:00.000Z` -> `YYYY-MM-DDT20:59:59.999Z`) ile sayfalı `POST` istekleri gönderilir.
   - **Karar Detayı (`getDocContent`):** Arama sonuçlarından dönen her kararın `documentId` değeri için `https://bedesten.adalet.gov.tr/emsal-karar/getDocContent` uç noktası sorgulanır.

2. **Ham JSON Kaydı ve Base64 İçerik Çözümleme:**
   - API'den gelen arama künyesi (`arama_kunyesi`) ve detay yanıtı (`detay`) hiçbir alan silinmeden olduğu gibi korunur.
   - Bedesten API'si karar metnini `detay.data.content` alanı içinde Base64 kodlu olarak gönderdiği için, bu alan otomatik olarak UTF-8 metne çözülür ve aynı nesnenin altına `detay.data.content_html` olarak eklenir.

3. **Hiyerarşik Dizinleme (`Yıl/Ay/Gün`):**
   - İndirilen her karar, karar tarihine göre `danistay_arsiv/YYYY/AA/GG/karar_{documentId}.json` dizin yapısında saklanır.

4. **SQLite Tabanlı Anlık Durum Takibi ve Disk Senkronizasyonu (`archive_state.db`):**
   - **`indirilen_kararlar` tablosu:** İndirilen her karar anında SQLite veritabanına ve RAM üzerindeki kümeye (`downloaded_ids`) işlenir; $O(1)$ hızında mükerrerlik kontrolü yapılır.
   - **`tamamlanan_gunler` tablosu:** Bir güne ait tüm sayfalar ve kararlar eksiksiz indirildiğinde o gün tamamlandı olarak işaretlenir. Program durdurulup yeniden başlatıldığında tamamlanmış günler doğrudan atlanır.
   - **`hatali_kararlar` tablosu:** Geçici ağ veya sunucu hatası nedeniyle detayı alınamayan kararlar, orijinal arama künyeleriyle birlikte bu tabloya kaydedilir ve tarama başında/sonunda otomatik olarak yeniden denenir.
   - **Otomatik Disk-DB Senkronizasyonu:** Program başlangıcında diskte var olan ancak veritabanına işlenmemiş `karar_*.json` dosyaları taranarak otomatik olarak SQLite'a indekslenir.

5. **WAF, Rate Limit (HTTP 429/403) ve Güvenlik Koruması:**
   - Erişim sınırı, Cloudflare/WAF veya Captcha yanıtları tespit edildiğinde aktif HTTP oturumu sıfırlanır (`_renew_session`) ve kademeli artan bekleme süreleriyle (exponential backoff) veri kaybı yaşanmadan devam edilir.

## Gereksinimler

- Python 3.8+
- `requests`
- `urllib3`

Kurulum:
```bash
pip install requests urllib3
```

## Kullanım Parametreleri

```bash
# Varsayılan ayarlarla (Bugünden 1970-01-01'e kadar geriye doğru) çalıştırma:
python danistay_indir.py

# Belirli bir tarih aralığında ve özel klasöre indirme:
python danistay_indir.py --start 2024-01-01 --end 2024-12-31 --delay 0.3 --dir danistay_2024

# Belirli sayıda karar indirip durdurma:
python danistay_indir.py --limit 500
```

### Parametre Açıklamaları

- `--start`: Arama alt sınır (en eski) tarihi (`YYYY-AA-GG`, varsayılan: `1970-01-01`).
- `--end`: Arama üst sınır (başlangıç/en yeni) tarihi (`YYYY-AA-GG`, varsayılan: Bugün).
- `--limit`: Maksimum indirilecek toplam karar sayısı sınırı.
- `--delay`: İstekler arası bekleme süresi (saniye, varsayılan: `0.25`).
- `--dir`: Kararların ve `archive_state.db` dosyasının kaydedileceği klasör (varsayılan: `danistay_arsiv`).

## Çıktı JSON Formatı (`karar_{documentId}.json`)

```json
{
  "arama_kunyesi": {
    "documentId": "1048576",
    "itemType": {
      "name": "DANISTAYKARAR",
      "description": "Danıştay Kararı"
    },
    "birimId": "...",
    "birimAdi": "Danıştay 2. Daire",
    "esasNoYil": 2023,
    "esasNoSira": 1450,
    "kararNoYil": 2024,
    "kararNoSira": 820,
    "kararTarihi": "2024-03-15T00:00:00.000+0000",
    "kararTarihiStr": "15.03.2024",
    "esasNo": "2023/1450",
    "kararNo": "2024/820"
  },
  "detay": {
    "data": {
      "content": "PGh0bWw+PGJvZHk+...",
      "mimeType": "text/html",
      "version": 1,
      "content_html": "<html><body>...T.C. DANIŞTAY İKİNCİ DAİRE...</body></html>"
    },
    "metadata": {
      "FMTY": "SUCCESS"
    }
  }
}
```

[Ana Dokümantasyona Dön](../README.md)
