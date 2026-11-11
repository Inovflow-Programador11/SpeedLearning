# 🔒 PC Lock API

API em Python para bloquear e desbloquear remotamente um PC Windows.

| Método | Endpoint  | Corpo                        | Efeito                                              |
|--------|-----------|------------------------------|-----------------------------------------------------|
| POST   | `/lock`   | `{"message": "texto"}`       | Bloqueia o PC e mostra a mensagem no ecrã de bloqueio (se já estiver bloqueado, só troca a mensagem) |
| POST   | `/unlock` | —                            | Desbloqueia o PC                                    |
| GET    | `/status` | —                            | Diz se está bloqueado, a mensagem e desde quando    |

Todos os endpoints exigem **autenticação** com um token no cabeçalho
`Authorization: Bearer <token>`. Respostas: `200` OK, `401` token em falta/errado,
`422` mensagem inválida, `429` demasiadas tentativas falhadas (10 em 5 min bloqueiam o IP).

## Como funciona o bloqueio

Quando `/lock` é chamado:

1. **Ecrã de bloqueio** — uma janela sem moldura, sempre no topo, cobre **todos os monitores**
   com o aviso "Este computador está bloqueado" e a mensagem enviada. A cada 300 ms volta a
   ficar por cima e a apanhar o foco.
2. **Teclado bloqueado** — um hook global de teclado (`WH_KEYBOARD_LL`) engole a tecla Windows,
   Alt+Tab, Alt+F4, Ctrl+Esc, Ctrl+Shift+Esc, Win+D, etc. O rato só "vê" o ecrã de bloqueio.
3. **Ctrl+Alt+Del neutralizado** — essa combinação não pode ser intercetada por nenhum programa,
   por isso são aplicadas políticas do Windows (HKCU) que retiram desse ecrã o Gestor de Tarefas,
   Bloquear, Terminar sessão, Alterar palavra-passe e Encerrar. "Cancelar" devolve ao ecrã de
   bloqueio (ver limitações sobre "Trocar de utilizador"). Os valores originais são guardados e repostos ao desbloquear.
4. **Sobrevive a reinícios** — o estado fica em `state.json`. Se o PC for reiniciado (ou o
   processo morrer) enquanto bloqueado, o bloqueio é reposto assim que a sessão arranca.

Opcionalmente pode definir um `unlock_pin` para permitir desbloquear localmente no próprio PC
(útil se a rede falhar). Sem PIN, o teclado fica totalmente bloqueado.

## Instalação no PC Windows

Requisitos: Windows 10/11 e Python 3.10+ (com a opção "tcl/tk" do instalador, que vem por defeito).

1. Copie a pasta `pc_lock_api` para o PC (ex.: `C:\pc_lock_api`).
2. Abra uma **PowerShell como Administrador** nessa pasta e execute:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\install.ps1
   ```

   O script:
   - instala as dependências (`fastapi`, `uvicorn`);
   - cria o `config.json` com um **token novo** (mostrado no ecrã — guarde-o);
   - cria a tarefa agendada `PCLockAPI` que arranca a API (sem janela, `pythonw.exe`) sempre que
     o utilizador inicia sessão, com privilégios elevados e reinício automático;
   - abre a porta (8765 por defeito) na firewall para redes de domínio/privadas.

   Para instalar para outro utilizador: `.\install.ps1 -User "PC\aluno"`.

> A API corre **dentro da sessão do utilizador** e não como serviço do Windows: os serviços
> correm na "sessão 0", que não consegue mostrar janelas no ecrã. Corre elevada para que o hook
> de teclado funcione mesmo quando uma aplicação de administrador está em primeiro plano.

Para remover: `.\uninstall.ps1` (como Administrador, com o PC desbloqueado).

### Configuração (`config.json`)

```json
{
  "token": "gerado com: python main.py --generate-token",
  "host": "0.0.0.0",
  "port": 8765,
  "unlock_pin": null,
  "ssl_certfile": null,
  "ssl_keyfile": null,
  "state_file": "state.json",
  "max_message_length": 500
}
```

Cada opção pode também vir de variáveis de ambiente: `PCLOCK_TOKEN`, `PCLOCK_HOST`,
`PCLOCK_PORT`, `PCLOCK_UNLOCK_PIN`, `PCLOCK_SSL_CERTFILE`, `PCLOCK_SSL_KEYFILE`,
`PCLOCK_STATE_FILE`. O token tem de ter pelo menos 24 caracteres.

**HTTPS:** o token viaja em cada pedido; fora de uma rede de confiança defina `ssl_certfile` e
`ssl_keyfile` (certificado PEM) para a API servir em `https://`.

## Exemplos de uso

PowerShell:

```powershell
$h = @{ Authorization = "Bearer O_SEU_TOKEN" }
Invoke-RestMethod -Method Post -Uri http://192.168.1.50:8765/lock -Headers $h `
  -ContentType "application/json" -Body '{"message": "Hora de jantar! Volta às 21h."}'
Invoke-RestMethod -Uri http://192.168.1.50:8765/status -Headers $h
Invoke-RestMethod -Method Post -Uri http://192.168.1.50:8765/unlock -Headers $h
```

curl:

```bash
curl -X POST http://192.168.1.50:8765/lock \
  -H "Authorization: Bearer O_SEU_TOKEN" -H "Content-Type: application/json" \
  -d '{"message": "Hora de jantar! Volta às 21h."}'
curl -X POST http://192.168.1.50:8765/unlock -H "Authorization: Bearer O_SEU_TOKEN"
```

Python:

```python
import requests
h = {"Authorization": "Bearer O_SEU_TOKEN"}
requests.post("http://192.168.1.50:8765/lock", json={"message": "Pausa!"}, headers=h)
requests.post("http://192.168.1.50:8765/unlock", headers=h)
```

## Executar manualmente / testar

```bash
pip install -r requirements.txt
python main.py --generate-token      # copie para config.json (ver config.example.json)
python main.py                       # no Windows: bloqueio real
python main.py --simulate            # qualquer SO: não bloqueia, só regista no log
pip install pytest httpx && python -m pytest tests
```

O registo de atividade fica em `pclock.log`.

## Limitações conhecidas

- Nenhum programa em modo utilizador consegue impedir **Ctrl+Alt+Del** nem o botão físico de
  energia; o primeiro fica sem opções úteis e, após um reinício, o bloqueio volta a ser aplicado.
- "Trocar de utilizador" continua disponível no ecrã de Ctrl+Alt+Del: quem souber a palavra-passe
  de **outra** conta pode entrar nela (o bloqueio é da sessão onde a API corre; ao voltar, o ecrã
  de bloqueio continua lá). Se o PC tiver uma só conta, isto não é um problema.
- Se ligar um monitor novo durante o bloqueio, esse monitor só fica coberto no próximo `/lock`
  depois de um `/unlock`.
