# Typing Bot

Monkeytype, 10FastFingers va boshqa yozish o'yinlarida matnni siz tanlagan
tezlik (WPM) va aniqlikda o'zi yozib beradigan kichik dastur.

| Sinov (Monkeytype, 30 s) | Sozlama | Sayt ko'rsatgan natija |
|---|---|---|
| 1 | 150 WPM, 100% | **150 WPM, 100%** |
| 2 | 250 WPM, 96% | **234 WPM, 96%** |

## Ishga tushirish

| Tizim | Nima qilish kerak |
|---|---|
| Windows | `start_windows.bat` ni ikki marta bosing. Python bo'lmasa, uni o'zi yuklab o'rnatadi |
| macOS | `start_mac.command` ni ikki marta bosing (Python 3.9–3.13 kerak: `brew install python-tk@3.12`) |
| Linux | `./start_mac_linux.sh` (kerak bo'lsa: `sudo apt install python3-tk python3-venv`) |

Birinchi ishga tushirishda kutubxonalar o'rnatiladi (1–2 daqiqa). Keyingi
safar dastur darhol ochiladi.

## Foydalanish

1. Ro'yxatdan saytni tanlang va **"1. Saytni ochish"** tugmasini bosing.
2. Saytda test turini tanlang (vaqt, so'zlar soni, til va hokazo).
3. **"2. Boshlash"** tugmasini bosing yoki brauzer ichida **F8** ni bosing.
   To'xtatish uchun **F9** ni bosing.

| Sozlama | Ma'nosi |
|---|---|
| Tezlik | 20–400 WPM (1 so'z = 5 belgi) |
| Aniqlik | 85–100%. 100% bo'lsa, hech qanday xato qilinmaydi |
| Xatolarni tuzatish | Xato harfni Backspace bilan o'chirib, to'g'risini yozadi |
| Maksimal tezlik | Hech qanday kutishsiz, brauzer ko'tara oladigan eng yuqori tezlikda yozadi |

**Universal rejim.** Bu rejim boshqa saytlar uchun. Matnni sichqoncha bilan
belgilang, so'ng "Boshlash" ni bosing va 3 soniya ichida yozish maydoniga bosing.

## Kompyuterga qanday moslashadi

* **Brauzer.** Avval Google Chrome'ni, keyin Microsoft Edge'ni qidiradi. Ikkalasi
  ham bo'lmasa, Chromium'ni o'zi yuklab oladi. Hech narsani sozlash shart emas.
* **Profil** `~/.typing-bot-profile` papkasida saqlanadi. Saytga bir marta
  kirsangiz (login), keyingi safar ham kirgan holatda qolasiz.
* **Ekrandan o'qimaydi.** Ekran o'lchami, masshtab, tema yoki shrift natijaga
  ta'sir qilmaydi. So'zlar to'g'ridan-to'g'ri sahifa kodidan (DOM) o'qiladi,
  tugmalar esa brauzerning o'z kiritish tizimi orqali bosiladi. Shuning uchun
  o'qishda xato bo'lmaydi va sichqoncha bilan boshqa oynalarga tegmaydi.
* **Tezlik.** Har bir belgi uchun vaqt alohida hisoblanadi. Kompyuter bir lahza
  sekinlashsa, keyingi belgilar bilan ortda qolgan vaqt to'ldiriladi. Shu tufayli
  natija tanlangan WPM ga yaqin chiqadi.

## Muhim

Dastur o'zingizning mashqingiz va sinovlaringiz uchun mo'ljallangan. Monkeytype
va boshqa saytlar qoidalariga ko'ra, bot natijalarini reyting (leaderboard)
jadvaliga yuborish taqiqlangan va akkaunt bloklanishiga olib kelishi mumkin.

## Fayllar

```
typing_bot.py        dastur (oyna + bot)
requirements.txt     playwright
start_windows.bat    Windows uchun ishga tushirgich
start_mac.command    macOS uchun ishga tushirgich
start_mac_linux.sh   Linux uchun ishga tushirgich
```
