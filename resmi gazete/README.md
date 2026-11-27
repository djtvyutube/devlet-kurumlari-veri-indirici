# Resmî Gazete Arşiv İndirici (`Python` + `wget`)

Resmî Gazete (`resmigazete.gov.tr`) arşivini dışarıdan ek bir kütüphaneye veya karmaşık kazıyıcılara ihtiyaç duymadan, doğrudan Python ile URL listesi üretip `wget` üzerinden toplu indirme yaklaşımı.

---

## Neden Bu Yöntem Kullanılıyor?

1. **Gizli ve Sıralı Arşiv Yapısı:** Resmî Gazete'nin eski sayıları (7 Şubat 1921 tarihli 1. sayıdan başlayarak) `/arsiv/{sayı}.pdf` dizininde doğrudan sayı numarasıyla barındırılır. `wget` tek başına ana sayfadan başlayarak bu PDF bağlantılarını keşfedemez; keşfetmeye çalışsa bile takvim/arama formları arkasında kaldığı için günler sürer.
2. **Sayfa İçine Gömülü Dosyalar (Fihrist Yapısı):** Yeni nesil Resmî Gazete sayıları (`/fihrist?tarih=YYYY-MM-DD`) günlük bir fihrist sayfası sunar ve o güne ait tüm kararları, yönetmelikleri, tebliğleri ve ekleri (`htm`, `html`, `pdf`, `png`, `gif`) kendi HTML kaynağının içine gömülü bağlantılar olarak verir.
3. **Python + `wget` İkilisi:**
   - **Python tek satırlık komutları**, `wget`'in tarayarak bulamayacağı veya bulması günler sürecek on binlerce doğrudan URL'yi saniyeler içinde üretip `.txt` dosyasına yazar.
   - **`wget` komutları** ise bu `.txt` dosyasındaki adresleri sırayla okuyup bağlantı kopmalarına dayanıklı (`--continue`) ve kaldığı yerden devam edebilen (`--no-clobber`) şekilde diske indirir.

---

## 1. Yöntem: Tam Sayı PDF Arşivini İndirme (7 Şubat 1921'den İtibaren)

7 Şubat 1921'de yayımlanan ilk sayıdan başlayarak Resmî Gazete'nin tek parça PDF gazeteler halinde yayımladığı tüm sayıları (`1.pdf` .. `35000.pdf`) indirir.
Bu inen dosyalar genellikle resmi gazewtenin gerçekten gazete olduğu osmanlı kayıtları dahil içerir.

### 1. Adım: Arşiv URL Listesini Oluşturma (`arsiv_urls.txt`)

```bash
python3 -c '
for n in range(1, 35001):
    print(f"https://www.resmigazete.gov.tr/arsiv/{n}.pdf")
' > arsiv_urls.txt
```

### 2. Adım: `wget` ile PDF'leri İndirme

```bash
wget --continue --no-clobber --no-check-certificate \
  --user-agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" \
  -i arsiv_urls.txt
```

---

## 2. Yöntem: Günlük Fihrist ve İçindeki Tüm Belgeleri İndirme (1 Ocak 1927'den Bugüne)

Yeni nesil site formatında yayımlanmış veya sonradan dijitalleştirilip fihriste eklenmiş hiçbir kaydı gözden kaçırmamak için **1 Ocak 1927** tarihinden bugüne kadar her günün fihrist adresini üretir.


> **Not:**Daha hızlı indirme işlemi için tarihin **1 Ocak 2000** yapılması önerilir resmi gazete bu tarihten sonra dijitalleşti. Python kodundaki `date(1927, 1, 1)` tarihini `date(2000, 1, 1)` olarak değiştirebilirsiniz. Hiçbir olası yeniden yayını kaçırmamak adına kodda `1927` seçilmiştir.
>
> **Disk Alanı Uyarısı:** `2.A` komutunu çalıştırmadan önce diskinizde yeterli boş yer olduğundan emin olun (**tahmini ~150 GB** yer kaplar).

### 1. Adım: Günlük Fihrist URL Listesini Oluşturma (`urls.txt`)

```bash
python3 -c '
from datetime import date, timedelta
d = date(1927, 1, 1)
end = date.today()
while d <= end:
    print(f"https://www.resmigazete.gov.tr/fihrist?tarih={d.isoformat()}")
    d += timedelta(days=1)
' > urls.txt
```

### 2.A Adım: Fihrist İçindeki Tüm Karar ve Ekleri (`pdf`, `htm`, `html`, `png`, `gif`) İndirme

Her günün fihrist sayfasına girer ve o sayfanın içine gömülü olan alt mevzuat/karar dosyalarını (`-l 1` derinlikle) gereksiz stil ve script dosyalarını eleyerek indirir:

> **Not:**Resmi gazeteye gelen yeni güncelleme ile birlikte anasayfalardaki html kodları bu komut ile indirilmemektedir sadece htm pdf png gibi resmi gazetede yayınlanan başlıklıklardaki dosyaları indirir.
>

```bash
wget -r -l 1 --continue --no-clobber \
  -e robots=off --no-check-certificate \
  --domains=resmigazete.gov.tr,www.resmigazete.gov.tr \
  -A "htm,html,pdf,png,gif" \
  -R "xml,mso,thmx,eot,woff,woff2,ttf,svg,css,js,ico" \
  --reject-regex="data\.url|%20\+|'\s*\+" \
  --user-agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" \
  -i urls.txt
```

### 2.B Adım: Sadece Ana Fihrist Sayfalarını İndirme

Alt belgelere ve eklere girmeden yalnızca günlük ana fihrist sayfalarını arşivlemek için kullanılır:

```bash
wget -P www.resmigazete.gov.tr --continue --no-clobber \
  -e robots=off --no-check-certificate \
  --user-agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36" \
  -i urls.txt
```

---

## Parametrelerin Açıklaması (Ne İşe Yarıyor?)

| Parametre | Ne İşe Yarar? |
| :--- | :--- |
| `-i arsiv_urls.txt` / `-i urls.txt` | İndirilecek URL adreslerini belirtilen metin dosyasından satır satır okur. |
| `--continue` (`-c`) | Yarım kalmış veya bağlantı kopması nedeniyle kesilmiş büyük PDF/HTML dosyalarını baştan indirmek yerine kaldığı bayttan tamamlar. |
| `--no-clobber` (`-nc`) | Diskte zaten tam olarak indirilmiş olan dosyaları tekrar indirmez ve üzerine yazmaz. İşlemi durdurup tekrar başlattığınızda kaldığı yerden devam etmesini sağlar. |
| `-r` (`--recursive`) | Sayfa içindeki bağlantıları takip ederek alt sayfalara/belgelere dallanma (özyinelemeli tarama) özelliğini açar. |
| `-l 1` (`--level=1`) | Bağlantı takip derinliğini `1` ile sınırlar. Yani sadece `urls.txt` içindeki günlük fihrist sayfasına ve doğrudan o fihristin içindeki kararlara/PDF'lere gider; başka sayfalara dağılmaz. |
| `-e robots=off` | `robots.txt` kısıtlamalarını devre dışı bırakarak arşiv alt dizinlerindeki belgelerin atlanmasını önler. |
| `--no-check-certificate` | Sunucu tarafındaki olası SSL sertifika zinciri hatalarında bağlantının kesilmesini engeller. |
| `--domains=resmigazete.gov.tr,...` | Taramanın sadece Resmî Gazete alan adları içinde kalmasını sağlar; dış sitelere verilen linkleri takip etmez. |
| `-A "htm,html,pdf,png,gif"` | **Kabul Listesi (Accept):** Yalnızca Resmî Gazete metinlerini (`htm`, `html`), tam/ek PDF dosyalarını (`pdf`) ve karar içindeki tablo/harita görsellerini (`png`, `gif`) indirir. |
| `-R "xml,mso,thmx,eot,..."` | **Red Listesi (Reject):** Font dosyaları (`woff`, `ttf`, `eot`), tasarım/script dosyaları (`css`, `js`, `ico`, `svg`) ve Word/Office kalıntı dosyalarını (`xml`, `mso`, `thmx`) indirmez; yüzlerce gigabaytlık çöp dosya birikmesini önler. |
| `--reject-regex="data\.url\|%20\+\|'\s*\+"` | Sayfa içindeki JavaScript kodlarında geçen dinamik string birleştirmelerini (`' + data.url + '` gibi) gerçek URL sanıp hatalı istek atmasını engeller. |
| `--user-agent="..."` | İstekleri standart bir masaüstü web tarayıcısı (Chrome) kimliğiyle gönderir. |

---

## Test ve Doğrulama Notu

Bu dokümanda yer alan tüm Python ve `wget` komutları, **bu deponun (repository) oluşturulduğu ve yayımlandığı tarihte test edilmiş, uçtan uca çalıştığı ve hedeflenen arşiv dosyalarını eksiksiz indirdiği onaylanmıştır.** Hedef kurumun ilerleyen tarihlerde web sitesi dizin yapısını, URL şemasını veya sunucu güvenlik kurallarını değiştirmesi durumunda komut parametrelerinde güncelleme yapılması gerekebilir.

## Yasal Uyarı ve Sorumluluk Reddi

Bu dokümanda yer alan komutlar ve betikler yalnızca eğitim, akademik araştırma ve kişisel arşivleme amaçlarıyla paylaşılmıştır. Cumhurbaşkanlığı İletişim Başkanlığı veya Resmî Gazete Müdürlüğü ile herhangi bir resmi bağı yoktur.

Paylaşılan komutlar yalnızca kamuya açık olarak yayımlanan Resmî Gazete sayılarına standart HTTP istekleriyle erişir; herhangi bir güvenlik duvarını, erişim kontrolünü veya kimlik doğrulama mekanizmasını aşmaz. Bu komutların çalıştırılmasından, hedef sunuculara gönderilecek istek sıklığından, ilgili kurumun kullanım koşullarına, 6698 sayılı Kişisel Verilerin Korunması Kanunu (KVKK), 5651 sayılı Kanun ve yürürlükteki mevzuata uygun hareket edilmesinden tamamen komutu çalıştıran kullanıcı sorumludur. Geliştirici ve katkıda bulunanlar, bu komutların doğrudan veya dolaylı kullanımından, sunucu erişim kısıtlamalarından veya veri saklama süreçlerinden doğabilecek hiçbir hukuki, idari, cezai veya teknik sonuçtan sorumlu tutulamaz.
