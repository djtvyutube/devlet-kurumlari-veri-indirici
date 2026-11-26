# Mevzuat Bilgi Sistemi (`mevzuat.gov.tr`) İndirici

Bu kod (**Alperen Enes Bayar**) tarafından yazılmıştır.

`www.mevzuat.gov.tr` üzerindeki kanun, KHK, tüzük, Cumhurbaşkanlığı kararnamesi, yönetmelik, tebliğ, Cumhurbaşkanı kararı ve genelgeleri indeksleyerek HTML metinlerini ve PDF dosyalarını indirir.

## Aşamalar

1. **`index`**: `MevzuatDatatable` uç noktasından tüm mevzuat türlerini tarar ve `manifest.jsonl` dosyasına yazar.
2. **`fetch`**: `manifest.jsonl` içindeki kayıtların HTML (`html/{tur}/{doc_id}.html`) ve PDF (`pdf/{tur}/{doc_id}.pdf`) dosyalarını indirir. İndirme kayıtlarını `fetch_log.jsonl` dosyasında tutar.
3. **`status`**: İndekslenen ve indirilen belgelerin istatistiklerini gösterir.

## Kullanım

```bash
pip install requests

python mevzuat_indirici.py index --out ./mevzuat_arsiv --page-size 100 --delay 1.0

python mevzuat_indirici.py fetch --out ./mevzuat_arsiv --workers 2 --delay 1.0

python mevzuat_indirici.py fetch --out ./mevzuat_arsiv --only html
python mevzuat_indirici.py fetch --out ./mevzuat_arsiv --only pdf

python mevzuat_indirici.py status --out ./mevzuat_arsiv
```
