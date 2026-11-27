# Devlet Kurumları Veri İndirici

Anayasa Mahkemesi, Danıştay, Yargıtay, Mevzuat Bilgi Sistemi (`mevzuat.gov.tr`) ve Sayıştay gibi kamu kurumlarının web sistemlerinden yargı kararlarını, mevzuat metinlerini ve PDF raporlarını indirmek için kullanılan Python betikleri.

Yargı kararı indiricileri (`ana_yasa_mahkemesi`, `danıştay`, `yargıtay`) kurumların API'lerinden dönen veriyi filtrelemeden, metin temizliği yapmadan ve dışarıdan alan eklemeden ham JSON olarak kaydeder.

## Klasör Yapısı

```text
devlet-kurumlari-veri-indirici/
├── ana_yasa_mahkemesi/
│   ├── aym_indirici.py
│   ├── aym_hatali_indirici.py
│   └── README.md
├── belge_indirici/
│   ├── dosya_indirici.py
│   ├── hatali_dosya_indirici.py
│   └── README.md
├── danıştay/
│   ├── danistay_indir.py
│   ├── danistay_hatali_indir.py
│   └── README.md
├── mevzuat/
│   ├── mevzuat_indirici.py
│   ├── mevzuat_hatali_indirici.py
│   └── README.md
├── resmi gazete/
│   └── README.md
├── sayıştay/
│   └── README.md
├── yargıtay/
│   ├── yargitay_indirici.py
│   ├── yargitay_hatali_indirici.py
│   └── README.md
├── requirements.txt
├── .gitignore
└── README.md
```

## Modüller

| Modül | Kaynak | Yöntem | Açıklama |
| :--- | :--- | :--- | :--- |
| **[Anayasa Mahkemesi](./ana_yasa_mahkemesi/README.md)** | `kararlarbilgibankasi.anayasa.gov.tr` | REST API + SQLite | Günlük sorgularla AYM kararlarını `Yıl/Ay/Gün` klasörlerine ham JSON olarak indirir. |
| **[Danıştay](./danıştay/README.md)** | `bedesten.adalet.gov.tr` | REST API + SQLite | Arama künyesini ve detay yanıtını (Base64'ten çözülmüş `content_html` ile) `Yıl/Ay/Gün` yapısında kaydeder. |
| **[Yargıtay](./yargıtay/README.md)** | `karararama-services.yargitay.gov.tr` | REST API + SQLite | Karar detay uç noktasından gelen yanıtı değiştirmeden `Yıl/Ay/Gün` klasörlerine JSON olarak yazar. |
| **[Mevzuat](./mevzuat/README.md)** | `mevzuat.gov.tr` | REST API | Kanun, KHK, Tüzük, Yönetmelik, Kararname ve Tebliğleri indeksler; HTML ve PDF dosyalarını indirir. *(Alperen Enes Bayar)* |
| **[Belge İndirici](./belge_indirici/README.md)** | Sayıştay vb. Kamu Siteleri | Selenium + Urllib | Kategori sayfalarındaki PDF raporları, kaynak HTML'i (`.source.html`) ve sayfa bilgilerini (`.metadata.json`) indirir. |
| **[Resmî Gazete](./resmi%20gazete/README.md)** | `resmigazete.gov.tr` | Python + `wget` | 1921'den itibaren tam sayı PDF arşivini ve günlük fihrist karar/eklerini indirme rehberi. |
| **[Sayıştay](./sayıştay/README.md)** | `sayistay.gov.tr` | `wget` + `dosya_indirici.py` | Statik bölümleri `wget` ile klonlama ve dinamik rapor kategorilerini `dosya_indirici.py` ile arşivleme rehberi. |

## Kurulum

```bash
git clone https://github.com/djtvyutube/devlet-kurumlari-veri-indirici.git
cd devlet-kurumlari-veri-indirici
pip install -r requirements.txt
```

Not: Yalnızca `belge_indirici` modülü kategori taraması için Chrome/Chromium ve Selenium kullanır. Diğer modüller doğrudan `requests` ile çalışır.

## Komut Satırı Parametreleri

### 1. Anayasa Mahkemesi (`ana_yasa_mahkemesi/aym_indirici.py` & `aym_hatali_indirici.py`)

```bash
python ana_yasa_mahkemesi/aym_indirici.py --start 2023-01-01 --end 2024-01-01 --output aymjson --delay 0.1 --limit 500
python ana_yasa_mahkemesi/aym_hatali_indirici.py --output aymjson --delay 0.15
```

| Parametre | Varsayılan | Açıklama |
| :--- | :--- | :--- |
| `--output`, `--dir` | `aymjson` | Çıktı ve `ilerleme_durumu.json` klasörü |
| `--start` | `1962-01-01` | En eski tarih (`YYYY-MM-DD`) |
| `--end` | Bugün / Son kalınan tarih | Başlangıç / en yeni tarih (`YYYY-MM-DD`) |
| `--limit` | Sınırsız | Maksimum indirilecek karar sayısı |
| `--delay` | `0.1` | İstekler arası bekleme süresi (saniye) |
| `--page-size` | `50` | Sayfa başına kayıt sayısı |

### 2. Danıştay (`danıştay/danistay_indir.py` & `danistay_hatali_indir.py`)

```bash
python danıştay/danistay_indir.py --start 2024-01-01 --end 2024-12-31 --dir danistay_arsiv --delay 0.25 --limit 500
python danıştay/danistay_hatali_indir.py --dir danistay_arsiv --delay 0.3
```

| Parametre | Varsayılan | Açıklama |
| :--- | :--- | :--- |
| `--dir`, `--output` | `danistay_arsiv` | Çıktı ve `archive_state.db` klasörü |
| `--start` | `1970-01-01` | En eski tarih (`YYYY-MM-DD`) |
| `--end` | Bugün | Başlangıç / en yeni tarih (`YYYY-MM-DD`) |
| `--limit` | Sınırsız | Maksimum indirilecek karar sayısı |
| `--delay` | `0.25` | İstekler arası bekleme süresi (saniye) |
| `--page-size` | `100` | Sayfa başına kayıt sayısı |

### 3. Yargıtay (`yargıtay/yargitay_indirici.py` & `yargitay_hatali_indirici.py`)

```bash
python yargıtay/yargitay_indirici.py --start 2023-01-01 --end 2023-12-31 --output yargitay_arsiv --delay 0.25 --limit 1000
python yargıtay/yargitay_hatali_indirici.py --output yargitay_arsiv --delay 0.25
```

| Parametre | Varsayılan | Açıklama |
| :--- | :--- | :--- |
| `--output`, `--dir` | `yargitay_arsiv` | Çıktı ve `archive_state.db` klasörü |
| `--start` | `1970-01-01` | En eski tarih (`YYYY-MM-DD`) |
| `--end` | Bugün | Başlangıç / en yeni tarih (`YYYY-MM-DD`) |
| `--limit` | Sınırsız | Maksimum indirilecek karar sayısı |
| `--delay` | `0.25` | İstekler arası bekleme süresi (saniye) |
| `--page-size` | `100` | Sayfa başına kayıt sayısı (en fazla 100) |

### 4. Belge İndirici (`belge_indirici/dosya_indirici.py` & `hatali_dosya_indirici.py`)

```bash
python belge_indirici/dosya_indirici.py -u https://www.sayistay.gov.tr/reports/category/13-faaliyet-genel-degerlendirme-raporlari -o indirilen_pdfler
python belge_indirici/hatali_dosya_indirici.py -o indirilen_pdfler
```

| Parametre | Varsayılan | Açıklama |
| :--- | :--- | :--- |
| `-u`, `--urls` | *(Zorunlu)* | Taranacak kategori adresleri (boşlukla ayrılmış birden fazla URL verilebilir) |
| `-s`, `--sayfa` | Sınırsız (Tüm sayfalar) | Her kategori için taranacak maksimum sayfa sayısı |
| `-o`, `--cikti` | `indirilen_pdfler` | PDF, `.source.html`, `.metadata.json` ve `hata.json` dosyalarının kaydedileceği klasör |
| `--category-pattern` | `/reports/category/` | Alt kategori linklerini tespit etmek için URL kalıbı |
| `--detail-pattern` | `/reports/` | Detay sayfalarını tespit etmek için URL kalıbı |

### 5. Mevzuat (`mevzuat/mevzuat_indirici.py` & `mevzuat_hatali_indirici.py`)

```bash
python mevzuat/mevzuat_indirici.py index --out mevzuat_arsiv --page-size 100 --delay 1.0
python mevzuat/mevzuat_indirici.py fetch --out mevzuat_arsiv --workers 2 --delay 1.0
python mevzuat/mevzuat_indirici.py status --out mevzuat_arsiv
python mevzuat/mevzuat_hatali_indirici.py --out mevzuat_arsiv --workers 2 --delay 1.0
```

| Alt Komut / Parametre | Varsayılan | Açıklama |
| :--- | :--- | :--- |
| `index` / `fetch` / `status` | *(Zorunlu)* | İndeksleme (`manifest.jsonl`), dosya indirme (`html` + `pdf`) veya durum özeti |
| `--out` | *(Zorunlu)* | Çıktı, `manifest.jsonl`, `fetch_log.jsonl` ve `hata.json` klasörü |
| `--workers` | `2` | `fetch` aşamasında paralel indirme iş parçacığı sayısı |
| `--delay` | `1.0` | İstekler arası bekleme süresi (saniye) |
| `--only` | Tümü (`html` + `pdf`) | Sadece `html` veya sadece `pdf` dosyalarını indirir |
| `--limit` | Sınırsız (`0`) | `fetch` aşamasında maksimum indirilecek dosya sayısı |
| `--retry-missing` | `False` | `fetch` aşamasında daha önce `missing` (404) dönmüş dosyaları da yeniden dener |

## Yasal Uyarı ve Sorumluluk Reddi

Bu depodaki yazılımlar yalnızca eğitim, akademik araştırma ve kişisel arşivleme amaçlarıyla geliştirilmiştir. İlgili kamu kurumları (Anayasa Mahkemesi, Danıştay, Yargıtay, Sayıştay, Cumhurbaşkanlığı Mevzuat Bilgi Sistemi vb.) ile herhangi bir resmi bağı veya ilişkisi yoktur.

Bu araçlar sadece kamuya açık sistemlerdeki verileri sorgular; herhangi bir erişim kontrolünü, güvenlik duvarını veya doğrulama mekanizmasını aşmaz. Yazılımların kullanımından, hedef sunuculara gönderilen istek sıklığından, ilgili kurumların kullanım koşullarına, 6698 sayılı Kişisel Verilerin Korunması Kanunu (KVKK), 5651 sayılı Kanun ve 5237 sayılı Türk Ceza Kanunu hükümlerine uygun hareket edilmesinden tamamen kullanıcı sorumludur. Geliştiriciler, bu yazılımların doğrudan veya dolaylı kullanımından doğabilecek hiçbir hukuki, idari, cezai veya teknik zarardan sorumlu tutulamaz.
