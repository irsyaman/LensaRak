# LensaRak — Panduan Rakaman Demo & Penerangan Sistem

Untuk explain kat kawan sebelum rakam, dan jadi script semasa rakaman esok.

---

## 1. Apa LensaRak, dalam 30 saat (bagi kawan)

**Masalah**: Petrosains ada 109 jenis item dalam storeroom. Bila staff/crew check-out untuk satu program, lepas habis, mereka **bulk return** — campak balik semua item sekaligus, dalam keadaan tergesa-gesa sebab program seterusnya dah nak mula. Kiraan manual masa tu = tempat paling senang silap kira/hilang stock.

**Penyelesaian**: Kamera tengok apa yang dicampak balik atas meja, sistem automatik kenal pasti item + kira kuantiti, update stock dalam database — kalau sistem tak yakin, dia tanya manusia dulu (bukan teka).

---

## 2. Flow sistem (macam mana dia jalan)

```
KAMERA (webcam / phone) 
    ↓
YOLOv8 detect item → dapat item_code + confidence score
    ↓
Confidence ≥ 0.75?
    ├── YA  → OCR cuba baca label kuantiti (kalau ada) → confirm dengan worker → terus update stock
    └── TAK → (kalau Gemini API key di-set) hantar gambar crop ke Gemini untuk second opinion
              ├── Gemini yakin → confidence naik, proceed macam atas
              └── Gemini pun tak yakin → masuk "needs_review", stock TAK diubah lagi
    ↓
lensarak_review.py — manusia semak needs_review queue lepas ni:
    [ENTER] betul, apply    [U] qty salah je    [C] item salah, tukar    [N] reject
```

**Prinsip utama** (ni yang nak explain kat kawan & sebut dalam video kalau sempat): sistem **tak pernah** teka bila tak yakin — dia sentiasa flag untuk manusia confirm dulu. Itu sebab reka bentuk dia dipanggil "trustworthy" — bukan automatik 100%, tapi automatik + safety net manusia.

---

## 3. Komponen dalam sistem (fail-fail dalam folder LensaRak)

| Fail | Fungsi |
|---|---|
| `LensaRak.bat` | Launcher — double-click, pilih menu (check-out / bulk return / review) |
| `lensarak_vision.py` | Kamera → detect → ledger. Live preview, SPACE untuk scan |
| `lensarak_ops.py` | Logic ledger — check_out, bulk_return, confidence gate, packaging conversion |
| `lensarak_review.py` | Console untuk clear queue needs_review (approve/correct/reject) |
| `lensarak_inventory.db` | Database SQLite — stock, transaction history |
| `best.pt` | Model YOLO yang dah di-train khas untuk 109 item ni |

**Ciri khas nak highlight**:
- **Camera-agnostic** — webcam ATAU phone (IP camera app) guna kod yang sama, tak payah tukar apa-apa.
- **Offline-first** — lepas training siap, seluruh sistem jalan tanpa internet (kecuali kalau Gemini second-opinion di-on).
- **OCR quantity reading** — baca label kuantiti atas bungkusan (contoh "Arduino UNO (30)") automatik.
- **Idempotent** — setiap transaction ada UUID unik, kalau accidentally scan dua kali/retry, stock tak double-deduct.
- **Audit trail jujur** — bila ada pembetulan (item silap detect), rekod asal (silap) KEKAL, satu rekod baru dibuat untuk pembetulan — tiada apa yang "hilang senyap".

---

## 4. Checklist SEBELUM rakam (buat malam ni / pagi esok, bukan masa dah nak record)

- [ ] Training YOLO dah siap — pastikan `best.pt` yang **terkini** (lepas training dataset gabungan) dah ada dalam folder LensaRak
- [ ] Test run cepat (tanpa record) — buka `LensaRak.bat`, cuba option 2 (bulk return webcam), pastikan detection nampak okay, tiada error
- [ ] Susun barang yang nak ditunjuk dalam video — campur beberapa item yang confidence tinggi + **sengaja** letak/pusing satu item supaya confidence rendah (untuk demo failure case)
- [ ] Kalau nak demo phone camera — buka app IP camera, confirm URL masih sama/updated
- [ ] Lighting cukup terang, background kemas (meja storeroom sebenar lagi elok)
- [ ] Laptop charged / plug in — jangan risau bateri mati tengah-tengah rakaman
- [ ] Clear queue `needs_review` dulu (run `lensarak_review.py` sekali sebelum record) — supaya masa demo nanti, item yang masuk review tu memang dari SCAN LIVE tu, bukan baki lama
- [ ] Tentukan siapa handle kamera/phone, siapa cakap/narrate, siapa taip kat laptop

---

## 5. Script rakaman (target ≤5 minit, SATU take berterusan, TAK edit)

> Ingat syarat organizer: continuous take, unedited, kena tunjuk bulk-return moment + sekurang-kurangnya SATU failure case dengan recovery dia.

**0:00 – 0:30 — Intro**
- Cakap ringkas: "Ni LensaRak, sistem computer vision untuk storeroom Petrosains, khas untuk masalah bulk-return."
- Tunjuk skrin sekali (folder LensaRak, atau terus kamera on)

**0:30 – 1:00 — Start sistem**
- Double-click `LensaRak.bat`
- Pilih **2** (Bulk return, webcam, live)
- Tunggu window kamera live preview muncul

**1:00 – 2:30 — Scan live (bulk return sebenar)**
- Letak beberapa item atas meja (campuran — yang confidence tinggi & yang senang dikenali)
- Tekan **SPACE**
- Biar sistem detect, confirm quantity satu-satu (tekan ENTER kalau betul, atau U kalau nak tukar qty)
- **Kalau ada beg berlabel** (contoh Arduino UNO "(30)") — tunjuk OCR auto-suggest quantity, confirm dengan ENTER — ni good visual moment
- Naratif: sebut confidence score yang muncul kat skrin, explain "di atas 0.75 = auto apply, bawah = pergi review"

**2:30 – 3:30 — Failure case (SENGAJA)**
- Letak/pusing SATU item supaya kamera struggle nak detect (angle pelik, sebahagian tertutup, atau item yang memang belum banyak training)
- Tekan SPACE, biar sistem detect dengan confidence rendah
- Naratif: "Nampak confidence ni rendah — sistem TAK auto-apply, dia route ke needs_review supaya manusia confirm dulu, bukan main teka."
- Tekan **Q** untuk keluar dari live preview

**3:30 – 4:30 — Recovery (review & correction)**
- Run `python lensarak_review.py` (atau pilih option 4 dalam `LensaRak.bat`)
- Tunjuk item yang tadi kena flag
- Demo salah SATU: `[ENTER]` kalau memang betul cuma confidence rendah, ATAU `[C]` kalau nak tunjuk correction flow (taip item_code betul)
- Naratif: "Stock baru diupdate LEPAS manusia confirm — bukan sebelum tu."

**4:30 – 5:00 — Wrap up**
- Ringkas balik: "Jadi LensaRak automatik untuk yang senang, dan flag untuk manusia bila tak yakin — itu design utama sistem ni."
- Habis rakaman.

---

## 6. Kalau ada masa lebih (bonus, tak wajib)

- Tunjuk sekali guna **phone sebagai kamera** (bukan webcam je) — untuk prove camera-agnostic claim.
- Tunjuk `check_out` mode pun (bukan bulk_return je) — kalau nak cover both scenario.

---

## 7. Lepas rakam

- Simpan video (jangan edit — syarat organizer kata unedited satu take)
- Ambil screenshot beberapa moment penting untuk Slide 8 portfolio ("Show It Working") — boleh screenshot terus dari video tu
- Update Slide 12 portfolio dengan angka baru (precision/recall/mAP50 dari training terkini) lepas kau dapat result
