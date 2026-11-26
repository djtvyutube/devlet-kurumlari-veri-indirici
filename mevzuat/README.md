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

## Yasal Uyarı ve Sorumluluk Reddi

Bu yazılım yalnızca eğitim, akademik araştırma ve kişisel arşivleme amaçlarıyla paylaşılmıştır. Cumhurbaşkanlığı Mevzuat Bilgi Sistemi (`mevzuat.gov.tr`) ile herhangi bir resmi bağı yoktur. Kamuya açık mevzuat metinlerinin indirilmesi ve kullanılması süreçlerinde ilgili kurumun kullanım koşullarına ve yürürlükteki mevzuata uyulması tamamen kullanıcının sorumluluğundadır. Kurum sunucularına aşırı yük bindirmemek için istek gecikme sürelerine (`--delay`) ve iş parçacığı sayısına (`--workers`) dikkat edilmelidir. Geliştiriciler, yazılımın kullanımından doğabilecek hiçbir hukuki, idari, cezai veya teknik sonuçtan sorumlu tutulamaz.
