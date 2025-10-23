# ScriptClone

ScriptClone é uma ferramenta Python para clonar álbuns ou mensagens de um grupo do Telegram para outro automaticamente. Ideal para quem quer transferir conteúdos de canais ou grupos de forma fácil e rápida.

---

## 🚀 Instalação Rápida

1. **Baixe e instale o Python 3.8 ou superior**  
   [Download Python](https://www.python.org/downloads/)

2. **Clone o repositório**  
   Abra o terminal ou prompt de comando e digite:
   ```bash
   git clone https://github.com/arthurxavieerr/telegramclonehot.git
   cd telegramclonehot
   ```

3. **Instale as dependências**  
   Digite:
   ```bash
   pip install .
   ```
   Ou:
   ```bash
   pip install -r requirements.txt
   ```

---

## ⚙️ Configuração

1. **Obtenha suas credenciais do Telegram**  
   - Acesse [https://my.telegram.org](https://my.telegram.org)
   - Faça login com seu número de telefone
   - Vá em "API Development Tools" e crie um app
   - Copie o **API ID** e o **API HASH**

2. **Abra o arquivo principal (`Eros_free.py`)**  
   - Preencha seus dados nas linhas (**Para facilitar de encontrar no código, APERTE CTRL+F e Pesquise por READER_API_ID, READER_API_HASH e etc...**):
     ```python
     READER_API_ID = seu_api_id_aqui
     READER_API_HASH = "seu_api_hash_aqui"
     SENDER_API_ID = seu_api_id_aqui
     SENDER_API_HASH = "seu_api_hash_aqui"
     ```
   - Configure os IDs dos grupos:
     ```python
     SOURCE_CHAT_ID = -1001234567890  # Coloque o ID do seu Grupo de origem, ou seja, o grupo que será clonado.
     TARGET_CHAT_ID = -1009876543210  # Coloque o ID do seu grupo de destino Grupo de destino, ou seja, o grupo que receberá as mensagens clonadas.
     ```
     Pesquise por "LINK DA PRIMEIRA MENSAGEM DO GRUPO AQUI" e insira a primeira mensagem do grupo que será clonado, para que o script inicie a clonagem a partir daquela mensagem.

   - Para pegar o link da primeira mensagem, clique nela no Telegram e escolha "Copiar link".

3. **(Opcional) Resetar o progresso**  
   - Apague o arquivo `transfer_progress.db` na pasta do projeto para começar do zero.

---

## ▶️ Como usar

No terminal, rode o script principal:

```bash
python Eros_free.py
```

---

## 📝 Dicas Importantes

- **Nunca compartilhe seu API ID ou API HASH!**
- Faça backup dos arquivos `.session` se trocar de computador.
- Veja o arquivo `transfer.log` para detalhes ou erros.
- Se precisar de ajuda, abra uma issue aqui no GitHub!

---

## 📖 Guia Completo

Veja o arquivo `GUIA.MD` para tutorial detalhado.

---

**Feito por [Clown171](https://github.com/SarfxxFx) — bom uso!**
