# Sistema E-commerce — Servidor de Licenças 2.0

Servidor Flask para ativação e validação das licenças do Sistema E-commerce.

## Recursos

- PostgreSQL em produção
- Login administrativo por variáveis de ambiente
- Criação de licenças
- Validade em dias ou vitalícia
- Bloqueio/desbloqueio
- Vinculação da licença ao primeiro computador
- Reset do computador pelo painel
- Ativação pelo EXE: `POST /api/activate`
- Validação pelo EXE: `POST /api/validate`
- Desativação: `POST /api/deactivate`
- Health check: `GET /health`

## Rodar localmente

Windows PowerShell:

```powershell
python -m venv venv
.env\Scripts\Activate.ps1
pip install -r requirements.txt
$env:FLASK_SECRET="uma-chave-local"
$env:ADMIN_USERNAME="admin"
$env:ADMIN_PASSWORD="sua-senha-local"
python app.py
```

Acesse `http://127.0.0.1:5000/login`.

Sem `DATABASE_URL`, o projeto usa `licenses.db` apenas para desenvolvimento local.

## Render

O `render.yaml` cria:

- Web Service Python
- PostgreSQL
- `DATABASE_URL` ligado automaticamente ao banco
- `FLASK_SECRET` gerado pelo Render

Depois do deploy, configure no Render:

- `ADMIN_USERNAME`
- `ADMIN_PASSWORD`

Não coloque a senha administrativa no código.

## API

Ativação:

```json
POST /api/activate
{
  "key": "JXCM-XXXX-XXXX-XXXX",
  "machine_id": "ID_DO_COMPUTADOR"
}
```

Validação:

```json
POST /api/validate
{
  "key": "JXCM-XXXX-XXXX-XXXX",
  "machine_id": "ID_DO_COMPUTADOR"
}
```

O servidor aceita uma ativação por licença. Para trocar de computador, o administrador deve usar **Resetar computador** no painel ou o EXE deve desativar a licença antes de ser removido.

## Migração do banco antigo

O `licenses.db` antigo deve ser mantido como backup. Não sobrescreva nem apague o arquivo até validarmos a nova instalação. A migração das licenças antigas será feita separadamente após o primeiro deploy do PostgreSQL.
