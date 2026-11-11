# Devlet Kurumları Veri İndirici

Türkiye Cumhuriyeti yargı organları (Anayasa Mahkemesi, Danıştay, Yargıtay) ve kamu denetim kurumlarının (Sayıştay vb.) web portallarından karar metinlerini, emsal kararları ve kurumsal raporları toplu olarak çekmek, temizlemek ve yapılandırılmış formatta yerel diske arşivlemek üzere geliştirilmiş veri toplama araç setidir.

## Projenin Amacı ve Kapsamı

Hukuk teknolojileri (LegalTech), yapay zeka/büyük dil modelleri (LLM) eğitimi, akademik çalışmalar veya yerel arşivleme amacıyla kamuya açık yargı kararlarının düzenli ve kesintisiz şekilde toplanmasını hedefler. 

Tüm modüller, yüksek hacimli indirme işlemlerinde sunucu yükünü dengede tutacak hız sınırlamaları (rate limit yönetimi), kesintiye uğrandığında kalınan yerden devam edebilme mekanizmaları ve mükerrer kayıtları eleyen önbellekleme sistemleriyle tasarlanmıştır.

## Proje Yapısı

```
devlet-kurumlari-veri-indirici/
│
├── ana_yasa_mahkemesi/
│   ├── aym_indirici.py       # AYM Kararlar Bilgi Bankası API tabanlı indirme betiği
│   └── README.md             # Modül dokümantasyonu ve detaylı çalışma mantığı
│
├── belge_indirici/
│   ├── dosya_indirici.py     # Sayıştay ve kamu kurumları PDF rapor indirme aracı
│   └── README.md             # Modül dokümantasyonu ve parametre rehberi
│
├── danıştay/
│   ├── danistay_kazima.py    # Danıştay arama portalı künye listesi çekici
│   ├── danistay_indir.py     # Toplanan künyelerin tam metinlerini çoklu iş parçacığıyla indirici
│   └── README.md             # İki aşamalı mimari rehberi ve kullanım detayları
│
├── yargıtay/
│   ├── yargitay_indirici.py  # Yargıtay portalı Selenium/SQLite tabanlı tarih dilimli arşivleyici
│   └── README.md             # Modül dokümantasyonu ve SQLite durum takip yapısı
│
├── requirements.txt          # Proje genelinde kullanılan kütüphaneler
├── .gitignore                # İndirilen veriler ve önbellek dosyaları için yoksayma kuralları
└── README.md                 # Genel proje tanıtımı ve yönlendirmeler
```

## Modüller ve Yönlendirmeler

| Modül | Hedef Kurum / Kaynak | Yöntem | Açıklama / Rehber |
| :--- | :--- | :--- | :--- |
| **[Anayasa Mahkemesi](./ana_yasa_mahkemesi/README.md)** | AYM Kararlar Bilgi Bankası | REST API + Günlük Tarama | Tarih bazlı geriye dönük sorgular ile tam metin AYM kararlarını Yıl/Ay/Gün yapısında JSON olarak arşivler. |
| **[Belge İndirici](./belge_indirici/README.md)** | Sayıştay ve Kamu Siteleri | Headless Selenium + Urllib | Kategori ve alt kategorileri otomatik keşfederek yayımlanan tüm PDF raporları diske kaydeder. |
| **[Danıştay](./danıştay/README.md)** | Danıştay Karar Arama | API + ThreadPoolExecutor | Önce sayfa bazında karar künyelerini çeker, ardından çoklu iş parçacığıyla metinleri ayrıştırıp Daire/Yıl dizininde arşivler. |
| **[Yargıtay](./yargıtay/README.md)** | Yargıtay Karar Arama | Headless Selenium + SQLite | Tarihi 5 günlük dilimlere (chunk) bölerek arama yapar; SQLite ile durumu takip edip kararları JSON olarak saklar. |

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

3. Selenium tabanlı çalışan modüller (`belge_indirici` ve `yargıtay`) için sisteminizde Google Chrome veya Chromium kurulu olmalıdır.

## Ortak Mimari Prensipler

- **Kesinti Koruması:** Her modül kendi içinde durum takip dosyası (`ilerleme_durumu.json`, `durum_detay.json`, `archive_state.db`) barındırır. Beklenmeyen bir ağ kesintisinde program yeniden başlatıldığında kaldığı yerden devam eder.
- **Mükerrerlik Önleme:** Bellek içi kümeler (RAM Sets) veya yerel veritabanı sorguları üzerinden O(1) hızında kontrol yapılarak aynı kararın ikinci defa indirilmesi engellenir.
- **Veri Temizliği:** HTML etiketleri (`<p>`, `<div>`, `<span>` vb.) ve kaçış karakterleri ayıklanarak salt karar metni elde edilir.
- **Hiyerarşik Arşivleme:** Veriler rastgele tek bir klasöre yığılmaz; `Kurum/Daire/Yıl` veya `Yıl/Ay/Gün` şeklinde sınıflandırılarak saklanır.

## Yasal Uyarı

Bu araçlar, kamuya açık resmi bilgi bankaları ve sorgu ekranlarındaki verilerin kişisel arşivleme, araştırma ve akademik amaçlarla toplanması için tasarlanmıştır. Veri toplama esnasında ilgili kurumların bilişim sistemlerine aşırı yük bindirmemek adına makul bekleme süreleri ve eşzamanlılık limitleri korunmalıdır. İlgili kurumların hizmet şartlarına ve Kişisel Verilerin Korunması Kanunu (KVKK) hükümlerine uyulması kullanıcının sorumluluğundadır.
