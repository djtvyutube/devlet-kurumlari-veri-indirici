# Yargıtay Karar İndirici

Yargıtay Karar Arama REST API'sini (`karararama-services.yargitay.gov.tr`) kullanarak kararları günümüzden geçmişe doğru gün gün tarar ve diske kaydeder.

## Çalışma Şekli

- **Arama:** `https://karararama-services.yargitay.gov.tr/api/kararlar` uç noktasından gün bazında sayfalı (`page`, `size`) karar listesini alır.
- **Detay:** Her karar için `https://karararama-services.yargitay.gov.tr/api/kararlar/karar/{id}` uç noktasını sorgular.
- **Ham JSON Kaydı:** Detay API'sinden dönen yanıt filtrelenmeden ve yeni alan eklenmeden olduğu gibi `yargitay_arsiv/YYYY/AA/GG/karar_{id}.json` (tarihsiz ise `yargitay_arsiv/tarihsiz/karar_{id}.json`) dosyasına yazılır.
- **Durum ve Hata Takibi (`archive_state.db`, `hata.json`, `idlist.json`):** İndirilen kararlar (`idlist.json` içinde kompakt `{"ID":["YYYY-MM-DD HH:MM:SS","konum"]}` formatında), tamamlanan günler, hata alınan kararlar (arama künyesi, kalıcı 404 ayrımı ve deneme sayısıyla), alınamayan arama sayfaları ve eksik kalan günler hem SQLite veritabanında (`archive_state.db`) hem de `hata.json` dosyasında HTTP kodu, exception ve yanıt gövdesiyle tutulur.
- **Hatalı Kararları Yeniden İndirme (`yargitay_hatali_indirici.py`):** Ana tarama scriptinden bağımsız olarak `hata.json` ve veritabanındaki hatalı arama sayfalarını ve karar ID'lerini tekrar dener (kalıcı 404 hatalarını varsayılan olarak atlar).

## Kullanım

```bash
pip install requests urllib3

python yargitay_indirici.py

python yargitay_indirici.py --start 2023-01-01 --end 2023-12-31 --delay 0.25 --page-size 100 --limit 1000 --output yargitay_2023

python yargitay_hatali_indirici.py --output yargitay_arsiv --delay 0.25
```

### Parametreler

| Parametre | Varsayılan | Açıklama |
| :--- | :--- | :--- |
| `--output`, `--dir` | `yargitay_arsiv` | Kararların ve `archive_state.db` veritabanının kaydedileceği klasör |
| `--start` | `1970-01-01` | En eski tarih (`YYYY-MM-DD`) |
| `--end` | Bugün | Başlangıç / en yeni tarih (`YYYY-MM-DD`) |
| `--limit` | Sınırsız | Maksimum indirilecek toplam karar sayısı |
| `--delay` | `0.25` | İstekler arası bekleme süresi (saniye) |
| `--page-size` | `100` | Sayfa başına kayıt sayısı (en fazla 100) |

## Çıktı Örneği

```json
{
  "id": "987654321",
  "daire": "1. Ceza Dairesi",
  "esasNo": "2022/100",
  "kararNo": "2023/250",
  "kararTarihi": "2023-02-15",
  "icerik": "<html>...</html>"
}
```

## Yasal Uyarı ve Sorumluluk Reddi

Bu yazılım yalnızca eğitim, akademik araştırma ve kişisel arşivleme amaçlarıyla geliştirilmiştir. Yargıtay Başkanlığı ile herhangi bir resmi bağı yoktur. Kamuya açık verilerin indirilmesi, saklanması ve işlenmesi süreçlerinde ilgili kurumun kullanım koşullarına, KVKK hükümlerine ve yürürlükteki mevzuata uyulması tamamen kullanıcının sorumluluğundadır. Kurum sunucularına aşırı yük bindirmemek için istek gecikme sürelerine (`--delay`) dikkat edilmelidir. Geliştirici, yazılımın kullanımından doğabilecek hiçbir hukuki, idari, cezai veya teknik sonuçtan sorumlu tutulamaz.
