# Danıştay Karar İndirici ve Arşivleyici

Bu modül, Danıştay Karar Arama Portalı (`karararama.danistay.gov.tr`) üzerinden karar künyelerini ve tam metinlerini toplayarak hiyerarşik bir JSON arşivi oluşturan iki aşamalı bir otomasyon sistemidir.

## Sistem Mimarisi ve Dosyalar

Sistem iki ana betikten oluşur:

1. `danistay_kazima.py`: Karar künye bilgilerini (ID, daire, esas/karar no, tarih) sayfa bazlı toplar.
2. `danistay_indir.py`: Toplanan künyelerdeki karar dokümanlarını indirir, HTML içeriğini temizler ve düzenli klasör yapısına yazar.

```
+---------------------+         +----------------------------+         +-------------------------------+
| danistay_kazima.py  |  --->   | danistay_detay_arsiv/*.json|  --->   | danistay_indir.py             |
| (Künyeleri listeler)|         | (Sayfa bazlı künyeler)     |         | (Metinleri çeker ve düzenler) |
+---------------------+         +----------------------------+         +-------------------------------+
                                                                                       |
                                                                                       v
                                                                       danistay_arsiv_duzenli/
                                                                         └── Daire/Yil/karar_{id}.json
```

## Çalışma Mantığı

### 1. Aşama: Karar Künyelerinin Kazınması (`danistay_kazima.py`)
- `https://karararama.danistay.gov.tr/aramadetaylist` POST uç noktası kullanılır.
- 2026 yılından 1950 yılına kadar geriye dönük yıllık tarama yürütülür.
- Güvenlik duvarı (WAF) sınırlarına takılmamak için yıl başına maksimum 1000 sayfa sorgulanır.
- Alınan her sayfa `danistay_detay_arsiv/danistay_{yil}_sayfa_{no}.json` olarak kaydedilir.
- `durum_detay.json` dosyası üzerinden kalınan yıl ve sayfa takip edilir; kesintide kaldığı yerden devam eder.

### 2. Aşama: Karar Metinlerinin İndirilmesi ve İşlenmesi (`danistay_indir.py`)
- `danistay_detay_arsiv` klasöründeki tüm künye JSON'ları taranır ve karar ID'leri tekilleştirilir.
- Halihazırda indirilmiş kararlar diskten taranıp belleğe alınarak mükerrer indirmeler elenir.
- `concurrent.futures.ThreadPoolExecutor` (varsayılan 6 worker) ile eşzamanlı olarak `getDokuman?id={id}` uç noktasından ham içerik çekilir.
- Dönen HTML içindeki gizli içerik (`#hiddencontent`) BeautifulSoup ile ayrıştırılarak düz metne dönüştürülür.
- Düzenli ifadeler (regex) ile metinden daire adı, esas numarası, karar numarası ve karar yılı tespit edilir.
- Karar, `danistay_arsiv_duzenli/{Daire_Adi}/{Yil}/karar_{id}.json` yolu altında saklanır.

## Gereksinimler

- Python 3.8+
- `requests`
- `beautifulsoup4`
- `urllib3`

Kurulum:
```bash
pip install requests beautifulsoup4 urllib3
```

## Kullanım

### Adım 1: Künyeleri Çekme
```bash
python danistay_kazima.py
```

### Adım 2: Metinleri İndirip Arşivleme
```bash
python danistay_indir.py
```

## Çıktı JSON Formatı

Her karar belgesi şu yapıda kaydedilir:
```json
{
    "dokuman_id": "123456",
    "daire": "Danıştay İkinci Daire",
    "esas_no": "2020/123 E.",
    "karar_no": "2021/456 K.",
    "tarih": "15.03.2021",
    "yil": "2021",
    "karar_metni": "T.C.\nDANIŞTAY\nİKİNCİ DAİRE\n..."
}
```

[Ana Dokümantasyona Dön](../README.md)
