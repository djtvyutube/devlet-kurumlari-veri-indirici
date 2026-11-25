# Yargıtay Karar Arama 2.0 Otomasyonu ve Arşivleyici

Bu modül, Yargıtay Karar Arama Portalı'nın yenilenen **REST API** altyapısını (`karararama-services.yargitay.gov.tr`) doğrudan kullanarak kararları günümüzden geçmişe doğru gün gün tarayan ve her kararın tam detay yanıtını hiçbir yapısal değişikliğe uğratmadan yerel diske arşivleyen veri toplama aracıdır.

## Çalışma Mantığı

1. **REST API 2.0 Doğrudan Erişim ve Saf JSON Kaydı:**
   - **Günlük Arama (`/api/kararlar`):** `https://karararama-services.yargitay.gov.tr/api/kararlar` uç noktasına `kararTarihiMin` ve `kararTarihiMax` parametreleriyle gün bazında sayfalı (`page`, `size`) `GET` istekleri gönderilir.
   - **Karar Detayı (`/api/kararlar/karar/{id}`):** Arama sonuçlarında bulunan her kararın benzersiz `id` değeri için doğrudan detay uç noktası sorgulanır.
   - **Ham Veri Korunumu:** Detay API'sinden dönen JSON yanıtı hiçbir karakter temizliği yapılmadan ve dışarıdan yapay alan eklenmeden birebir API'den geldiği şekliyle diske kaydedilir.

2. **Hiyerarşik Dizinleme (`Yıl/Ay/Gün`):**
   - İndirilen kararlar `yargitay_arsiv/YYYY/AA/GG/karar_{id}.json` dizin yapısında arşivlenir.

3. **SQLite Tabanlı Durum Takibi ve Disk Senkronizasyonu (`archive_state.db`):**
   - **`indirilen_kararlar` tablosu:** Başarıyla indirilen her karar anında SQLite veritabanına ve RAM üzerindeki `downloaded_ids` kümesine (set) eklenir; mükerrer indirmeler $O(1)$ hızında engellenir.
   - **`tamamlanan_gunler` tablosu:** Bir güne ait tüm sayfalar ve kararlar hatasız indirildiğinde ilgili tarih tamamlandı olarak işaretlenir. Kesinti sonrası yeniden başlatmada tamamlanmış günler atlanır.
   - **`hatali_kararlar` tablosu:** Ağ kesintisi veya sunucu hatası nedeniyle detayı çekilemeyen kararlar yarım/özet veriyle kaydedilmez; bunun yerine `hatali_kararlar` tablosuna işlenir ve tarama başında/sonunda otomatik olarak tekrar denenerek tam detaylarıyla kurtarılır.
   - **Otomatik Disk-DB Senkronizasyonu:** Başlangıçta çıktı klasörü taranarak diskte mevcut olup veritabanında kaydı bulunmayan `karar_*.json` dosyaları otomatik olarak SQLite'a aktarılır.

4. **WAF, Rate Limit (HTTP 429/403) ve Erişim Sınırı Koruması:**
   - Sunucudan hız sınırı, WAF engeli veya beklenmeyen HTML yanıtı döndüğünde HTTP oturumu otomatik olarak yenilenir (`_renew_session`) ve artan bekleme süreleriyle (exponential backoff) istek tekrarlanır.

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
python yargitay_indirici.py

# Belirli bir tarih aralığında ve özel çıktı klasörüyle çalıştırma:
python yargitay_indirici.py --start 2023-01-01 --end 2023-12-31 --delay 0.25 --page-size 100 --output yargitay_2023
```

### Parametre Açıklamaları

- `--output`: Kararların ve `archive_state.db` veritabanının kaydedileceği ana dizin (varsayılan: `yargitay_arsiv`).
- `--start`: Arama alt sınır (en eski) tarihi (format: `YYYY-MM-DD`, varsayılan: `1970-01-01`).
- `--end`: Arama üst sınır (başlangıç/en yeni) tarihi (format: `YYYY-MM-DD`, varsayılan: Bugün).
- `--delay`: İstekler arası bekleme süresi (saniye, varsayılan: `0.25`).
- `--page-size`: Sayfa başına çekilecek kayıt sayısı (varsayılan: `100`).

## Çıktı JSON Formatı (`karar_{id}.json`)

Her karar belgesi, `/api/kararlar/karar/{id}` uç noktasından dönen ham API yanıtı olarak kaydedilir:

```json
{
  "id": "987654321",
  "daire": "1. Ceza Dairesi",
  "esasNo": "2022/100",
  "kararNo": "2023/250",
  "kararTarihi": "2023-02-15",
  "icerik": "<html>...T.C. YARGITAY 1. CEZA DAİRESİ...</html>"
}
```

[Ana Dokümantasyona Dön](../README.md)
