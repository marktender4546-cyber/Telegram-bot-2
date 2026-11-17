REMIND ME — RENDER.COM GA JOYLASH
=================================

MUHIM XAVFSIZLIK ESLATMASI
--------------------------
Avvalgi ZIP ichidagi config.py faylida BOT TOKEN va ADMIN ID ochiq yozilgan edi.
Token endi oshkor bo'lgan deb hisoblang: Telegram'da @BotFather orqali /revoke
buyrug'i bilan eski tokenni bekor qiling va yangi token oling. Yangi tokenni
hech qachon kodga yoki GitHub repozitoriyga yozmang.

1. GITHUB GA YUKLASH
--------------------
1) ZIP ichidagi Remind_Me_Bot papkasini oching.
2) Barcha fayllarni GitHub'dagi yangi yoki mavjud repozitoriyga yuklang.
3) .venv, ma'lumotlar bazasi va bot.log repozitoriyga kiritilmagan.

2. RENDER GA DEPLOY QILISH
--------------------------
1) https://render.com saytiga kiring va GitHub hisobingizni ulang.
2) New > Blueprint ni tanlang.
3) Remind_Me_Bot repozitoriyasini tanlang.
4) Render render.yaml faylini o'qib Background Worker yaratadi.
5) Deploy tugashini kuting.

3. MAXFIY O'ZGARMASLARNI KIRITISH
----------------------------------
Render Dashboard > remind-me-telegram-bot > Environment bo'limida:

BOT_TOKEN = @BotFather bergan YANGI token
ADMIN_ID  = Sizning Telegram raqamli ID'ingiz

Bu qiymatlar Render'da saqlanadi; Python fayllarida mavjud emas.
ADMIN_ID ixtiyoriy: 0 bo'lsa admin buyruqlari o'chiriladi.

4. MA'LUMOTLAR BAZASI VA DOIMIY ISHLASH
---------------------------------------
render.yaml faylida /var/data manziliga 1 GB Persistent Disk biriktirilgan.
SQLite bazasi DATABASE_PATH=/var/data/remind_me.db orqali shu diskda saqlanadi.
Shu sababli oddiy deploy/restart vaqtida eslatmalar saqlanib qoladi.

Eslatma: Persistent Disk va Background Worker uchun Render pullik Starter
rejasi talab qilinishi mumkin. Bepul web service doimiy bot polling uchun mos
emas (uxlab qolishi mumkin). Render tariflari o'zgarishi mumkin.

5. BOTNI TEKSHIRISH
-------------------
Render > Logs bo'limida "Remind Me ishga tushdi" yozuvini tekshiring.
Telegram'da botga /start yuboring. Render'dagi worker ishlayotganida
eslatmalar belgilangan vaqtda yuboriladi.

6. LOKAL ISHGA TUSHIRISH
-------------------------
Windows CMD:
  py -m venv .venv
  .venv\Scripts\activate
  pip install -r requirements.txt
  set BOT_TOKEN=YANGI_TOKEN
  set ADMIN_ID=123456789
  python bot.py

Linux/macOS:
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -r requirements.txt
  export BOT_TOKEN="YANGI_TOKEN"
  export ADMIN_ID="123456789"
  python bot.py

FAYLLAR
-------
bot.py                 Telegram bot kodi
config.py              Sozlamalarni faqat environment variables'dan oladi
database.py            SQLite ma'lumotlar bazasi
scheduler.py           Sana-vaqt hisob-kitoblari
requirements.txt       Python kutubxonalari
render.yaml            Render Background Worker va Persistent Disk sozlamasi
README_UZ.txt          Asl qo'llanma
README_RENDER_UZ.txt   Render uchun qo'llanma

Botning funksiyalari va mavjud buyruqlari o'zgartirilmagan.
