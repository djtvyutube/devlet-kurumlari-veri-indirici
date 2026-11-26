# Yargıtay Karar İndirici

Yargıtay Karar Arama REST API'sini (`karararama-services.yargitay.gov.tr`) kullanarak kararları günümüzden geçmişe doğru gün gün tarar ve diske kaydeder.

## Çalışma Şekli

- **Arama:** `https://karararama-services.yargitay.gov.tr/api/kararlar` uç noktasından gün bazında sayfalı (`page`, `size`) karar listesini alır.
- **Detay:** Her karar için `https://karararama-services.yargitay.gov.tr/api/kararlar/karar/{id}` uç noktasını sorgular.
- **Ham JSON Kaydı:** Detay API'sinden dönen yanıt filtrelenmeden ve yeni alan eklenmeden olduğu gibi `yargitay_arsiv/YYYY/AA/GG/karar_{id}.json` dosyasına yazılır.
- **Durum Takibi (`archive_state.db`):** İndirilen kararlar, tamamlanan günler ve hata alınan kararlar SQLite veritabanında tutulur. Kesinti sonrası kaldığı yerden devam eder.

## Kullanım

```bash
pip install requests urllib3

# Varsayılan (Bugünden 1970-01-01'e kadar)
python yargitay_indirici.py

# Tarih aralığı, limit ve klasör belirterek
python yargitay_indirici.py --start 2023-01-01 --end 2023-12-31 --delay 0.25 --page-size 100 --limit 1000 --output yargitay_2023
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
