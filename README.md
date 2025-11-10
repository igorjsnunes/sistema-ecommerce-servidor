SISTEMA-E-COMMERCE - Servidor de Licenças (Render-ready)

1) Como testar localmente
   python -m venv venv
   source venv/bin/activate  # (Linux/Mac) ou venv\Scripts\activate (Windows)
   pip install -r requirements.txt
   export FLASK_SECRET="uma_chave_segura"
   python app.py
   Acesse: http://localhost:5000/login
   usuário: admin
   senha: slipknot66

2) Deploy no Render
   - Crie conta em https://render.com
   - Crie um novo Web Service -> Connect a repo (ou drag & drop)
   - Se usar GitHub, conecte e selecione o repo com estes arquivos
   - Render detecta Python e roda o startCommand do render.yaml
   - Após deploy, terá HTTPS automático e um domínio como:
     https://sistema-ecommerce.onrender.com
