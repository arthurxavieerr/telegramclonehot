# Guia Rápido de Instalação e Configuração do ScriptClone

Este guia foi feito para ser fácil de entender, mesmo se você nunca programou antes. Siga os passos abaixo para instalar e configurar o ScriptClone!

---

## Instalação

### 1. Instale o Python

- Baixe e instale o Python 3.8 ou superior: [Download Python](https://www.python.org/downloads/)
- Durante a instalação, marque a opção "Add Python to PATH".

### 2. Baixe o ScriptClone

Abra o programa "Prompt de Comando" ou "Terminal" e digite:

```bash
git clone https://github.com/SarfxxFx/ScriptClone.git
cd ScriptClone
```

Se aparecer erro, instale o Git: [Download Git](https://git-scm.com/downloads)

### 3. Instale os arquivos necessários

No terminal, digite:

```bash
pip install .
```
Ou, se preferir:

```bash
pip install -r requirements.txt
```

Pronto! O ScriptClone está instalado.

---

## Configuração

### 1. Pegue suas credenciais do Telegram

- Entre em [https://my.telegram.org](https://my.telegram.org)
- Faça login com seu número de telefone
- Vá em "API Development Tools", crie um app e anote o **API ID** e o **API HASH**

### 2. Edite o arquivo do projeto

- Abra o arquivo `Eros_free.py` usando o bloco de notas ou outro editor.
- Preencha os campos com suas credenciais:

```python
READER_API_ID = seu_api_id_aqui
READER_API_HASH = "seu_api_hash_aqui"

SENDER_API_ID = seu_api_id_aqui
SENDER_API_HASH = "seu_api_hash_aqui"
```
Você pode usar a mesma conta para leitura e envio, se quiser.

### 3. Configure os grupos e a mensagem

- Procure por `SOURCE_CHAT_ID` e coloque o ID do grupo de onde quer copiar as mensagens.
- Procure por `TARGET_CHAT_ID` e coloque o ID do grupo para onde as mensagens vão.
- Para copiar o link da primeira mensagem no Telegram, clique (ou toque e segure) nela e escolha "Copiar link". Cole esse link no campo solicitado.

Exemplo:
```python
SOURCE_CHAT_ID = -1001234567890
TARGET_CHAT_ID = -1009876543210
# Exemplo de primeiro link de mensagem
https://t.me/c/2519203567/2549
```

---

## Como resetar o progresso

Se quiser começar de novo, apague o arquivo chamado `transfer_progress.db` que fica na mesma pasta do projeto.

---

## Executando o ScriptClone

No terminal, digite:

```bash
python Eros_free.py
```
Ou:
```bash
python main.py
```
Escolha o arquivo que deseja usar.

---

## Dicas Importantes

- **Nunca compartilhe seu API ID ou API HASH!**
- Faça backup do arquivo `.session` se trocar de computador.
- Se aparecer algum erro, confira o arquivo `transfer.log` na pasta do projeto.
- Se precisar de ajuda, peça ao desenvolvedor ou abra uma issue no GitHub.

---

**Pronto! Seu ScriptClone está configurado. Bom uso!**