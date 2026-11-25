# Devlet Kurumları Veri İndirici

Anayasa Mahkemesi, Danıştay, Yargıtay, Mevzuat Bilgi Sistemi (`mevzuat.gov.tr`) ve Sayıştay gibi kamu kurumlarının web sistemlerinden yargı kararlarını, mevzuat metinlerini ve PDF raporlarını indirmek için kullanılan Python betikleri.

Yargı kararı indiricileri (`ana_yasa_mahkemesi`, `danıştay`, `yargıtay`) kurumların API'lerinden dönen veriyi filtrelemeden, metin temizliği yapmadan ve dışarıdan alan eklemeden ham JSON olarak kaydeder.

## Klasör Yapısı

```text
devlet-kurumlari-veri-indirici/
├── ana_yasa_mahkemesi/
│   ├── aym_indirici.py          # AYM günlük karar indirici
│   ├── aym_hatali_indirici.py   # Hata veren AYM kararlarını tekrar indirme betiği
│   └── README.md
├── belge_indirici/
│   ├── dosya_indirici.py        # Sayıştay ve kamu siteleri PDF rapor indirici
│   └── README.md
├── danıştay/
│   ├── danistay_indir.py        # Danıştay (Bedesten API) günlük karar indirici
│   └── README.md
├── mevzuat/
│   ├── mevzuat_indirici.py      # mevzuat.gov.tr HTML/PDF indirici (Alperen Enes Bayar)
│   └── README.md
├── yargıtay/
│   ├── yargitay_indirici.py     # Yargıtay REST API günlük karar indirici
│   └── README.md
├── requirements.txt
├── .gitignore
└── README.md
```

## Modüller

| Modül | Kaynak | Yöntem | Açıklama |
| :--- | :--- | :--- | :--- |
| **[Anayasa Mahkemesi](./ana_yasa_mahkemesi/README.md)** | `kararlarbilgibankasi.anayasa.gov.tr` | REST API | Günlük sorgularla AYM kararlarını `Yıl/Ay/Gün` klasörlerine ham JSON olarak indirir. |
| **[Danıştay](./danıştay/README.md)** | `bedesten.adalet.gov.tr` | REST API + SQLite | Arama künyesini ve detay yanıtını (Base64'ten çözülmüş `content_html` ile) `Yıl/Ay/Gün` yapısında kaydeder. |
| **[Yargıtay](./yargıtay/README.md)** | `karararama-services.yargitay.gov.tr` | REST API + SQLite | Karar detay uç noktasından gelen yanıtı değiştirmeden `Yıl/Ay/Gün` klasörlerine JSON olarak yazar. |
| **[Mevzuat](./mevzuat/README.md)** | `mevzuat.gov.tr` | REST API | Kanun, KHK, Tüzük, Yönetmelik, Kararname ve Tebliğleri indeksler; HTML ve PDF dosyalarını indirir. *(Alperen Enes Bayar)* |
| **[Belge İndirici](./belge_indirici/README.md)** | Sayıştay vb. Kamu Siteleri | Selenium + Urllib | Kategori sayfalarındaki PDF raporları ve sayfa bilgilerini (`.metadata.json`) indirir. |

## Kurulum

```bash
git clone https://github.com/djtvyutube/devlet-kurumlari-veri-indirici.git
cd devlet-kurumlari-veri-indirici
pip install -r requirements.txt
```

Not: Yalnızca `belge_indirici` modülü kategori taraması için Chrome/Chromium ve Selenium kullanır. Diğer modüller doğrudan `requests` ile çalışır.
