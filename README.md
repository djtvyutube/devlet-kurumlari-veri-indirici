# Devlet Kurumları Veri İndirici

Türkiye Cumhuriyeti yüksek yargı organları (**Anayasa Mahkemesi, Danıştay, Yargıtay**), **Cumhurbaşkanlığı Mevzuat Bilgi Sistemi** (`mevzuat.gov.tr`) ve kamu denetim kurumlarının (**Sayıştay** vb.) resmi web portallarından ve REST API'lerinden yargı kararlarını, mevzuat metinlerini ve kurumsal raporları toplu olarak çekmek ve yerel diske arşivlemek üzere geliştirilmiş veri toplama araç setidir.

## Projenin Amacı ve Kapsamı

Hukuk teknolojileri (LegalTech), yapay zeka / büyük dil modelleri (LLM) eğitimi, akademik araştırmalar ve yerel arşivleme amacıyla kamuya açık yargı kararlarının, mevzuatların ve kamu raporlarının düzenli, kesintisiz ve **veri bütünlüğü bozulmadan (ham API yanıtlarıyla)** toplanmasını hedefler.

Tüm modüller; yüksek hacimli indirme işlemlerinde sunucu yükünü dengede tutacak hız sınırlamaları (rate limit / WAF yönetimi), kesintiye uğrandığında kalınan yerden devam edebilme mekanizmaları (SQLite / JSON durum takibi), hatalı veya eksik kalan kayıtları sonradan kurtarma sistemleri ve mükerrer indirmeleri önleyen bellek/disk önbellekleme mimarisiyle tasarlanmıştır.

## Proje Yapısı

```text
devlet-kurumlari-veri-indirici/
│
├── ana_yasa_mahkemesi/
│   ├── aym_indirici.py          # AYM Kararlar Bilgi Bankası REST API tabanlı günlük karar indirici
│   ├── aym_hatali_indirici.py   # Hata veren / indirilemeyen AYM kararlarını kurtarma betiği
│   └── README.md                # Modül dokümantasyonu ve API veri yapısı rehberi
│
├── belge_indirici/
│   ├── dosya_indirici.py        # Sayıştay ve kamu kurumları PDF rapor ve metadata indirme aracı
│   └── README.md                # Modül dokümantasyonu ve CLI parametre rehberi
│
├── danıştay/
│   ├── danistay_indir.py        # Danıştay 2.0 (Bedesten/UYAP API) SQLite tabanlı günlük karar arşivleyici
│   └── README.md                # Modül dokümantasyonu ve çıktı şeması
│
├── mevzuat/
│   ├── mevzuat_indirici.py      # Mevzuat Bilgi Sistemi (mevzuat.gov.tr) HTML/PDF arşivleyici (Yazar: Alperen Enes Bayar)
│   └── README.md                # 3 aşamalı (index, fetch, status) kullanım rehberi
│
├── yargıtay/
│   ├── yargitay_indirici.py     # Yargıtay 2.0 REST API ve SQLite tabanlı günlük karar arşivleyici
│   └── README.md                # Modül dokümantasyonu ve SQLite durum takip yapısı
│
├── requirements.txt             # Proje genelinde kullanılan Python kütüphaneleri
├── .gitignore                   # İndirilen veri klasörleri, SQLite/JSON durum dosyaları ve önbellek kuralları
├── LICENSE                      # Lisans dosyası
└── README.md                    # Genel proje tanıtımı ve modül yönlendirmeleri
```

## Modüller ve Yönlendirmeler

| Modül | Hedef Kurum / Kaynak | Yöntem | Açıklama / Rehber |
| :--- | :--- | :--- | :--- |
| **[Anayasa Mahkemesi](./ana_yasa_mahkemesi/README.md)** | AYM Kararlar Bilgi Bankası | REST API + Günlük Tarama | Günümüzden 1962'ye doğru günlük sorgularla AYM kararlarını hiçbir şekillendirme yapmadan ham JSON olarak `Yıl/Ay/Gün` yapısında arşivler; eksik/hatalı ID'leri ayrıca kurtarır. |
| **[Danıştay](./danıştay/README.md)** | Danıştay Karar Arama (Bedesten API) | REST API + SQLite | UYAP/Bedesten API üzerinden gün gün kararları çeker; arama künyesini, ham detay yanıtını ve Base64'ten çözülmüş HTML metnini `Yıl/Ay/Gün` dizininde arşivler. |
| **[Yargıtay](./yargıtay/README.md)** | Yargıtay Karar Arama 2.0 | REST API + SQLite | Yenilenen Yargıtay REST API servisini gün gün tarar; SQLite ile durumu takip ederek her kararın ham API detay yanıtını `Yıl/Ay/Gün` dizininde JSON olarak saklar. |
| **[Mevzuat](./mevzuat/README.md)** | Mevzuat Bilgi Sistemi (`mevzuat.gov.tr`) | REST API + ThreadPoolExecutor | Kanun, KHK, Tüzük, Yönetmelik, Kararname ve Tebliğleri indeksler (`manifest.jsonl`); tam metin HTML ve resmi PDF belgelerini indirir. *(Alperen Enes Bayar tarafından yazılmıştır)* |
| **[Belge İndirici](./belge_indirici/README.md)** | Sayıştay ve Kamu Siteleri | Headless Selenium + Urllib | Kategori ve alt kategorileri otomatik keşfederek yayımlanan tüm PDF raporları ve SHA-256/kaynak üstverilerini (`.metadata.json`) diske kaydeder. |

## Kurulum

Projeyi çalıştırmadan önce sisteminizde Python 3.8 veya üzeri bir sürümün kurulu olduğundan emin olunuz.

1. Depoyu yerel ortamınıza klonlayın:
   ```bash
   git clone https://github.com/djtvyutube/devlet-kurumlari-veri-indirici.git
   cd devlet-kurumlari-veri-indirici
   ```

2. Gerekli Python kütüphanelerini yükleyin:
   ```bash
   pip install -r requirements.txt
   ```

3. Sadece `belge_indirici` modülü dinamik kategori keşfi için Headless Selenium kullanır; bu modülü kullanacaksanız sisteminizde Google Chrome veya Chromium kurulu olmalıdır. Diğer tüm modüller (`ana_yasa_mahkemesi`, `danıştay`, `yargıtay`, `mevzuat`) doğrudan `requests` ile HTTP/REST API üzerinden çalışır.

## Ortak Mimari Prensipler

- **Saf (Ham) API Verisi Korunumu:** Yargı kararı indiricileri (`ana_yasa_mahkemesi`, `danıştay`, `yargıtay`), API'den dönen JSON yanıtlarını yapay alanlar uydurmadan ve metin içeriklerini bozmadan orijinal haliyle kaydeder (Danıştay modülü ek olarak yalnızca Base64 kodlu `content` alanını okunabilir `content_html` olarak çözer).
- **Kesinti Koruması ve Hata Kurtarma:** Her modül kendi içinde durum takip yapısı (`archive_state.db`, `ilerleme_durumu.json`, `manifest.jsonl` / `fetch_log.jsonl`) barındırır. Beklenmeyen bir ağ kesintisinde veya `Ctrl+C` ile durdurulduğunda kalınan yerden devam eder; hata veren kararlar kayıt altına alınarak otomatik veya yardımcı betikle yeniden indirilir.
- **Mükerrerlik Önleme:** Bellek içi kümeler (`set`) ve SQLite indeksleri üzerinden $O(1)$ karmaşıklığında kontrol yapılarak aynı kararın veya belgenin ikinci kez indirilmesi engellenir.
- **Hiyerarşik Arşivleme:** Veriler tek bir klasöre yığılmaz; `YYYY/AA/GG/karar_{id}.json` veya mevzuat türü bazında sınıflandırılarak saklanır.

## Yasal Uyarı

Bu araçlar, kamuya açık resmi bilgi bankaları ve sorgu ekranlarındaki verilerin kişisel arşivleme, araştırma ve akademik amaçlarla toplanması için tasarlanmıştır. Veri toplama esnasında ilgili kurumların bilişim sistemlerine aşırı yük bindirmemek adına makul bekleme süreleri ve eşzamanlılık limitleri korunmalıdır. İlgili kurumların hizmet şartlarına ve Kişisel Verilerin Korunması Kanunu (KVKK) hükümlerine uyulması kullanıcının sorumluluğundadır.
