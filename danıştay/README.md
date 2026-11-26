# Danıştay Karar İndirici

Danıştay Karar Arama portalının kullandığı Bedesten API (`bedesten.adalet.gov.tr`) üzerinden gün gün kararları çeker ve yerel diske kaydeder.

## Çalışma Şekli

- **Arama:** `https://bedesten.adalet.gov.tr/emsal-karar/getEmsalKarar` uç noktasına günlük tarih aralığıyla sayfalı `POST` isteği atar.
- **Detay:** Her kararın `documentId` değeri için `https://bedesten.adalet.gov.tr/emsal-karar/getDocContent` uç noktasını sorgular.
- **Ham Veri ve Base64 Çözümleme:** API'den gelen arama künyesi ve detay yanıtı filtrelenmeden olduğu gibi kaydedilir. Yalnızca `detay.data.content` içindeki Base64 kodlu karar metni çözülerek `detay.data.content_html` alanına eklenir.
- **Klasör Yapısı:** `danistay_arsiv/YYYY/AA/GG/karar_{documentId}.json`
- **Durum Takibi (`archive_state.db`):** İndirilen kararlar, tamamlanan günler ve hata veren kararlar SQLite veritabanında tutulur. Program yeniden başlatıldığında tamamlanan günleri atlar ve hata tablosundaki kararları tekrar dener.

## Kullanım

```bash
pip install requests urllib3

python danistay_indir.py

python danistay_indir.py --start 2024-01-01 --end 2024-12-31 --delay 0.3 --dir danistay_2024

python danistay_indir.py --limit 500
```

### Parametreler

| Parametre | Varsayılan | Açıklama |
| :--- | :--- | :--- |
| `--dir`, `--output` | `danistay_arsiv` | Kararların ve `archive_state.db` veritabanının kaydedileceği klasör |
| `--start` | `1970-01-01` | En eski tarih (`YYYY-MM-DD`) |
| `--end` | Bugün | Başlangıç / en yeni tarih (`YYYY-MM-DD`) |
| `--limit` | Sınırsız | Maksimum indirilecek toplam karar sayısı |
| `--delay` | `0.25` | İstekler arası bekleme süresi (saniye) |
| `--page-size` | `100` | Sayfa başına çekilecek kayıt sayısı |

## Çıktı Örneği

```json
{
  "arama_kunyesi": {
    "documentId": "1048576",
    "itemType": {
      "name": "DANISTAYKARAR",
      "description": "Danıştay Kararı"
    },
    "birimAdi": "Danıştay 2. Daire",
    "esasNo": "2023/1450",
    "kararNo": "2024/820",
    "kararTarihiStr": "15.03.2024"
  },
  "detay": {
    "data": {
      "content": "PGh0bWw+PGJvZHk+...",
      "mimeType": "text/html",
      "version": 1,
      "content_html": "<html><body>...</body></html>"
    },
    "metadata": {
      "FMTY": "SUCCESS"
    }
  }
}
```

## Yasal Uyarı ve Sorumluluk Reddi

Bu yazılım yalnızca eğitim, akademik araştırma ve kişisel arşivleme amaçlarıyla geliştirilmiştir. Danıştay Başkanlığı veya Adalet Bakanlığı ile herhangi bir resmi bağı yoktur. Kamuya açık verilerin indirilmesi, saklanması ve işlenmesi süreçlerinde ilgili kurumun kullanım koşullarına, KVKK hükümlerine ve yürürlükteki mevzuata uyulması tamamen kullanıcının sorumluluğundadır. Kurum sunucularına aşırı yük bindirmemek için istek gecikme sürelerine (`--delay`) dikkat edilmelidir. Geliştirici, yazılımın kullanımından doğabilecek hiçbir hukuki, idari, cezai veya teknik sonuçtan sorumlu tutulamaz.
