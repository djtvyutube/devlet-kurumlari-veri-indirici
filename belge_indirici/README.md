# Kamu Kurumları Belge ve Rapor İndirici

Sayıştay ve benzeri kamu kurumlarının web sitelerindeki rapor kategorilerini Selenium ile tarar, bulunan PDF raporlarını ve her dosyanın kaynak/başlık bilgilerini içeren `.metadata.json` dosyalarını indirir.

## Özellikler

- Verilen URL'lerdeki `/reports/category/` bağlantılarından alt kategorileri bulur ve `?page=X` parametresiyle sayfaları gezer.
- Detay sayfalarındaki PDF indirme bağlantılarını, sayfa başlığını, açıklamayı ve tarihi alır.
- Dosyaları `<orijinal_ad>__[<kurum>__<kategori>__<tarih>].pdf` adıyla kaydeder.
- Her PDF'in yanına `<dosya_adi>.pdf.metadata.json` ve ana klasöre `indirilen_dosyalar_metadata.json` kataloğu yazar.
- Diskte zaten var olan PDF'leri tekrar indirmez.

## Kullanım

```bash
pip install selenium

# Tek kategori indirme
python dosya_indirici.py -u https://www.sayistay.gov.tr/reports/category/13-faaliyet-genel-degerlendirme-raporlari -s 2 -o faaliyet_raporlari

# Birden fazla kategori indirme
python dosya_indirici.py -u URL1 URL2 -s 3 -o indirilen_pdfler
```

### Parametreler

- `-u, --urls`: Taranacak kategori adresleri (boşlukla ayrılmış).
- `-s, --sayfa`: Her kategori için taranacak sayfa sayısı (varsayılan: `1`).
- `-o, --cikti`: Çıktı klasörü (varsayılan: `indirilen_pdfler`).
