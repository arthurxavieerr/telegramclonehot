#!/usr/bin/env python3
"""
Versão otimizada do script de transferência de álbuns do Telegram.
Sistema de 3 filas: Download (10), Upload (10), Envio (1), com ordem absoluta e sem ultrapassagem.
MODIFICADO: Agora usa duas contas - uma para ler e outra para enviar.
ADICIONADO: Modo rápido (sem intervalo) e modo lento (1 hora entre envios)
CORRIGIDO: Retomada após interrupção
"""

import asyncio
import json
import logging
import os
import shutil
import sqlite3
import sys
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
import telethon
print("Telethon version:", telethon.__version__)

from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument, Message
from telethon.errors import FloodWaitError

@dataclass
class MediaInfo:
    message_id: int
    grouped_id: Optional[int]
    date: datetime
    media_type: str
    file_size: int
    file_name: str
    caption: Optional[str]
    local_path: Optional[str] = None
    downloaded: bool = False
    uploaded: bool = False

@dataclass
class AlbumInfo:
    grouped_id: int
    medias: List[MediaInfo]
    caption: Optional[str]
    date: datetime
    processed: bool = False
    downloaded: bool = False
    uploaded: bool = False
    sent: bool = False  # NOVO: flag para marcar se foi enviado

    @property
    def total_size(self) -> int:
        return sum(media.file_size for media in self.medias)

@dataclass
class QueuePosition:
    """Classe para rastrear posições nas filas"""
    album_id: int
    original_index: int  # Índice na ordem cronológica original
    download_started: bool = False
    download_completed: bool = False
    upload_started: bool = False
    upload_completed: bool = False
    send_completed: bool = False

class ProgressTracker:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_database()

    def init_database(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS albums (
                    grouped_id INTEGER PRIMARY KEY,
                    album_data TEXT,
                    processed BOOLEAN DEFAULT 0,
                    downloaded BOOLEAN DEFAULT 0,
                    uploaded BOOLEAN DEFAULT 0,
                    sent BOOLEAN DEFAULT 0,
                    date_created TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS progress (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_albums_status ON albums(processed, downloaded, uploaded, sent)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_albums_date ON albums(date_created)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_progress_key ON progress(key)")

    async def save_album(self, album: AlbumInfo):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO albums 
                (grouped_id, album_data, processed, downloaded, uploaded, sent, date_created)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                album.grouped_id,
                json.dumps(asdict(album), default=str),
                album.processed,
                album.downloaded,
                album.uploaded,
                album.sent,
                album.date.isoformat()
            ))

    async def save_albums_batch(self, albums: List[AlbumInfo]):
        with sqlite3.connect(self.db_path) as conn:
            data = [
                (
                    album.grouped_id,
                    json.dumps(asdict(album), default=str),
                    album.processed,
                    album.downloaded,
                    album.uploaded,
                    album.sent,
                    album.date.isoformat()
                )
                for album in albums
            ]
            conn.executemany("""
                INSERT OR REPLACE INTO albums 
                (grouped_id, album_data, processed, downloaded, uploaded, sent, date_created)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, data)

    async def load_albums(self) -> Dict[int, AlbumInfo]:
        albums = {}
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT album_data FROM albums 
                ORDER BY date_created ASC
            """)
            for (album_data,) in cursor.fetchall():
                try:
                    data = json.loads(album_data)
                    medias = []
                    for media_data in data['medias']:
                        media_data['date'] = datetime.fromisoformat(media_data['date'])
                        medias.append(MediaInfo(**media_data))
                    data['medias'] = medias
                    data['date'] = datetime.fromisoformat(data['date'])
                    # Garantir que o campo 'sent' existe (para compatibilidade com DBs antigas)
                    if 'sent' not in data:
                        data['sent'] = False
                    album = AlbumInfo(**data)
                    albums[album.grouped_id] = album
                except Exception as e:
                    logging.warning(f"Erro carregando álbum: {e}")
                    continue
        return albums

    async def update_progress(self, key: str, value: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO progress (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            """, (key, value))

    async def get_progress(self, key: str) -> Optional[str]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT value FROM progress WHERE key = ?", (key,))
            result = cursor.fetchone()
            return result[0] if result else None

class TelegramAlbumTransfer:
    def __init__(self, 
                 # Credenciais da conta de leitura
                 reader_api_id: int,
                 reader_api_hash: str,
                 reader_session_name: str,
                 # Credenciais da conta de envio
                 sender_api_id: int,
                 sender_api_hash: str,
                 sender_session_name: str,
                 # IDs dos chats
                 source_chat_id: int,
                 target_chat_id: int,
                 # Outras configurações
                 temp_dir: str = "./temp_media",
                 max_download_queue: int = 10,
                 max_upload_queue: int = 10,
                 progress_db: str = "./transfer_progress.db",
                 batch_size: int = 1000,
                 slow_mode: bool = False):  # NOVO: modo lento
        
        # Cliente para leitura/download
        self.reader_client = TelegramClient(reader_session_name, reader_api_id, reader_api_hash)
        # Cliente para envio
        self.sender_client = TelegramClient(sender_session_name, sender_api_id, sender_api_hash)
        
        self.source_chat_id = source_chat_id
        self.target_chat_id = target_chat_id
        self.temp_dir = Path(temp_dir)
        self.max_download_queue = max_download_queue
        self.max_upload_queue = max_upload_queue
        self.batch_size = batch_size
        self.progress_tracker = ProgressTracker(progress_db)
        self.albums: Dict[int, AlbumInfo] = {}
        self.upload_delay = 7.0
        self.last_upload_time = 0
        self.request_delay = 3
        self.timeout = 60
        self.flood_wait_multiplier = 1.7
        self.max_retries = 5
        self.download_delay = 0.8
        
        # NOVO: configurações de modo lento
        self.slow_mode = slow_mode
        self.send_interval = 3600.0 if slow_mode else 0.0  # 1 hora = 3600 segundos
        self.last_send_time = 0
        
        self.download_queue = deque()
        self.upload_queue = deque()
        self.send_queue = deque()
        self.download_active = set()
        self.upload_active = set()
        self.send_active = None
        self.download_lock = asyncio.Lock()
        self.upload_lock = asyncio.Lock()
        self.send_lock = asyncio.Lock()
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('transfer.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        self.floodwait_log_count = 0

    async def safe_telegram_call(self, client, func, *args, **kwargs):
        """Versão modificada que aceita qual cliente usar"""
        for attempt in range(10):
            try:
                return await func(*args, **kwargs)
            except FloodWaitError as e:
                wait_time = getattr(e, 'seconds', 60)
                self.floodwait_log_count += 1
                self.logger.warning(f"[FloodWait #{self.floodwait_log_count}] FloodWait de {wait_time}s em {func.__name__}, aguardando...")
                await asyncio.sleep(wait_time + 1)
            except Exception as e:
                self.logger.error(f"Erro inesperado em {func.__name__}: {e}")
                if attempt == 9:
                    raise
                await asyncio.sleep(3)

    async def get_first_message_from_link(self, message_link: str) -> Optional[Message]:
        try:
            parts = message_link.rstrip("/").split('/')
            message_id = int(parts[-1])
            self.logger.info(f"Obtendo primeira mensagem pelo ID: {message_id}")
            message = await self.safe_telegram_call(
                self.reader_client,
                self.reader_client.get_messages,
                self.source_chat_id,
                ids=message_id
            )
            if message:
                self.logger.info(f"Primeira mensagem encontrada: ID={message.id}, Data={message.date}")
                return message
            else:
                self.logger.error("Mensagem inicial não encontrada")
                return None
        except Exception as e:
            self.logger.error(f"Erro ao obter primeira mensagem: {e}")
            return None

    async def start(self):
        # Conectar ambos os clientes
        await self.reader_client.start()
        self.logger.info("Cliente de leitura conectado")
        
        await self.sender_client.start()
        self.logger.info("Cliente de envio conectado")
        
        # NOVO: Mostrar modo de operação
        mode_text = "MODO LENTO (1 hora entre envios)" if self.slow_mode else "MODO RÁPIDO (sem intervalo)"
        self.logger.info(f"===== {mode_text} =====")
        
        self.temp_dir.mkdir(exist_ok=True)
        try:
            self.albums = await self.progress_tracker.load_albums()
            last_message_id = await self.progress_tracker.get_progress("last_processed_message")
            if self.albums:
                self.logger.info(f"Carregados {len(self.albums)} álbuns do progresso anterior")
            if last_message_id != "completed":
                self.logger.info("Iniciando escaneamento completo e ordenado a partir da primeira mensagem desejada...")
                await self.scan_messages_chronological()
            else:
                self.logger.info("Escaneamento já foi concluído anteriormente")
            await self.pipeline_strict_order()
            self.logger.info("Transferência concluída com sucesso!")
        except Exception as e:
            self.logger.error(f"Erro durante a transferência: {e}")
            raise
        finally:
            await self.cleanup()

    async def scan_messages_chronological(self):
        self.logger.info("Iniciando escaneamento cronológico completo...")
        first_message_link = "LINK DA PRIMEIRA MENSAGEM DO GRUPO AQUI"       # <-- COLOQUE A PRIMEIRA MENSAGEM DO GRUPO QUE VOCÊ QUER CLONAR AQUI
        first_message = await self.get_first_message_from_link(first_message_link)
        if not first_message:
            self.logger.error("Não foi possível obter a mensagem inicial. Abortando.")
            return

        try:
            chat_info = await self.safe_telegram_call(
                self.reader_client,
                self.reader_client.get_entity, 
                self.source_chat_id
            )
            self.logger.info(f"Chat de origem: {getattr(chat_info, 'title', 'Chat privado')}")
            
            # Também obter info do chat de destino com o cliente de envio
            target_info = await self.safe_telegram_call(
                self.sender_client,
                self.sender_client.get_entity,
                self.target_chat_id
            )
            self.logger.info(f"Chat de destino: {getattr(target_info, 'title', 'Chat privado')}")
        except Exception as e:
            self.logger.warning(f"Não foi possível obter informações dos chats: {e}")
        
        self.logger.info("Coletando todas as mensagens com mídia a partir do ID inicial...")
        all_messages = []
        message_count = 0
        batch_count = 0

        try:
            async for message in self.reader_client.iter_messages(
                self.source_chat_id,
                min_id=first_message.id - 1,
                reverse=True
            ):
                message_count += 1
                batch_count += 1
                if hasattr(message, 'media') and message.media:
                    all_messages.append(message)
                if batch_count >= 200:
                    await asyncio.sleep(1.2)
                    batch_count = 0
                if message_count % 5000 == 0:
                    self.logger.info(f"Coletadas {message_count} mensagens...")
        except FloodWaitError as e:
            wait_time = getattr(e, 'seconds', 60)
            self.logger.warning(f"FloodWait durante coleta: aguardando {wait_time}s")
            await asyncio.sleep(wait_time + 1)
        except Exception as e:
            self.logger.error(f"Erro inesperado durante coleta de mensagens: {e}")
            raise

        self.logger.info(f"Coletadas {len(all_messages)} mensagens com mídia de {message_count} mensagens totais")

        if all_messages:
            self.logger.info(f"Primeira mensagem: ID={all_messages[0].id}, Data={all_messages[0].date}")
            self.logger.info(f"Última mensagem: ID={all_messages[-1].id}, Data={all_messages[-1].date}")
            for m in all_messages[:20]:
                self.logger.info(f"MsgID {m.id} - date={m.date} grouped_id={getattr(m, 'grouped_id', None)}")
        
        all_messages.sort(key=lambda x: (x.date.timestamp(), x.id))
        self.logger.info(f"Mensagens ordenadas cronologicamente")
        await self.process_messages_for_albums(all_messages)
        await self.progress_tracker.update_progress("last_processed_message", "completed")
        self.logger.info(f"Escaneamento concluído: {len(self.albums)} álbuns encontrados")

    async def process_messages_for_albums(self, messages: List[Message]):
        self.logger.info(f"Processando {len(messages)} mensagens para identificar álbuns...")
        album_groups = defaultdict(list)
        media_count = 0

        for i, message in enumerate(messages):
            if i % 1000 == 0:
                self.logger.info(f"Processando mensagem {i+1}/{len(messages)}")
            media_info = await self.extract_media_info_safe(message)
            if not media_info:
                continue
            media_count += 1
            if media_info.grouped_id:
                album_groups[media_info.grouped_id].append(media_info)
            else:
                solo_id = f"solo_{media_info.message_id}"
                album_groups[solo_id].append(media_info)

        for grouped_id, medias in album_groups.items():
            medias.sort(key=lambda x: x.message_id)

        self.logger.info(f"Encontradas {media_count} mídias, {len(album_groups)} grupos potenciais")

        valid_albums = 0
        batch_albums = []
        sorted_groups = sorted(album_groups.items(), 
                             key=lambda x: (min(m.date.timestamp() for m in x[1]), 
                                          min(m.message_id for m in x[1])))
        
        for grouped_id, medias in sorted_groups:
            if isinstance(grouped_id, int) or (isinstance(grouped_id, str) and not grouped_id.startswith("solo_")):
                if len(medias) > 10:
                    self.logger.warning(f"Álbum {grouped_id} com mais de 10 mídias ({len(medias)}). Limitando às 10 primeiras.")
                    medias = medias[:10]
            album_id = grouped_id if isinstance(grouped_id, int) else medias[0].message_id
            album = AlbumInfo(
                grouped_id=album_id,
                medias=medias,
                caption=next((m.caption for m in medias if m.caption), None),
                date=medias[0].date,
                sent=False
            )
            self.albums[album.grouped_id] = album
            batch_albums.append(album)
            valid_albums += 1
            if valid_albums <= 5:
                self.logger.info(
                    f"Álbum {valid_albums}: ID={album.grouped_id}, "
                    f"Data={medias[0].date.strftime('%Y-%m-%d %H:%M:%S')}, "
                    f"Primeiro ID={medias[0].message_id}, "
                    f"Mídias={len(medias)}"
                )
            if len(batch_albums) >= 100:
                await self.progress_tracker.save_albums_batch(batch_albums)
                batch_albums = []
        if batch_albums:
            await self.progress_tracker.save_albums_batch(batch_albums)
        self.logger.info(f"Álbuns válidos criados: {valid_albums}")

    async def pipeline_strict_order(self):
        sorted_albums = sorted(
            self.albums.values(),
            key=lambda x: (
                x.date.timestamp(),
                min(m.message_id for m in x.medias)
            )
        )
        total = len(sorted_albums)
        self.logger.info(f"Iniciando pipeline com {total} álbuns em ordem cronológica rigorosa")
        self.logger.info("REGRAS: Download(10)->Upload(10)->Envio(1), SEM ultrapassagem")
        self.logger.info("Ordem: Timestamp -> Menor ID primeiro")
        
        if self.slow_mode:
            self.logger.info(f"MODO LENTO ATIVADO: Intervalo de {self.send_interval/60:.0f} minutos entre envios")
        
        queue_positions = {}
        for i, album in enumerate(sorted_albums):
            queue_positions[album.grouped_id] = QueuePosition(
                album_id=album.grouped_id,
                original_index=i
            )
            if i < 5:
                self.logger.info(
                    f"Álbum na posição {i}: "
                    f"Data={album.date.strftime('%Y-%m-%d %H:%M:%S')}, "
                    f"Primeiro ID={min(m.message_id for m in album.medias)}, "
                    f"Enviado={album.sent}"
                )
        
        # CORREÇÃO: Verificar se os arquivos locais ainda existem antes de restaurar o estado
        self.download_queue = deque()
        self.upload_queue = deque()
        self.send_queue = deque()
        
        for album in sorted_albums:
            if album.sent:
                # Já foi enviado, pular
                pos = queue_positions[album.grouped_id]
                pos.download_completed = True
                pos.upload_completed = True
                pos.send_completed = True
                continue
            
            # NOVO: Verificar se os arquivos ainda existem
            files_exist = all(
                media.local_path and os.path.exists(media.local_path) 
                for media in album.medias 
                if media.downloaded
            )
            
            if album.uploaded and files_exist:
                # Já foi upado e arquivos existem - vai para fila de envio
                self.send_queue.append(album.grouped_id)
                pos = queue_positions[album.grouped_id]
                pos.download_completed = True
                pos.upload_completed = True
                self.logger.info(f"[RETOMADA] Álbum {album.grouped_id} restaurado na fila de ENVIO")
            elif album.downloaded and files_exist:
                # Já foi baixado e arquivos existem - vai para fila de upload
                self.upload_queue.append(album.grouped_id)
                pos = queue_positions[album.grouped_id]
                pos.download_completed = True
                self.logger.info(f"[RETOMADA] Álbum {album.grouped_id} restaurado na fila de UPLOAD")
            else:
                # Arquivos não existem ou não foi baixado - precisa baixar novamente
                if album.downloaded and not files_exist:
                    self.logger.warning(f"[RETOMADA] Arquivos do álbum {album.grouped_id} não encontrados - será baixado novamente")
                    # Resetar flags de download
                    album.downloaded = False
                    album.uploaded = False
                    for media in album.medias:
                        media.downloaded = False
                        media.local_path = None
                    await self.progress_tracker.save_album(album)
                
                self.download_queue.append(album.grouped_id)
        
        self.logger.info(f"[RETOMADA] Filas iniciais: Download={len(self.download_queue)}, Upload={len(self.upload_queue)}, Envio={len(self.send_queue)}")
        
        await asyncio.gather(
            self.download_manager(sorted_albums, queue_positions),
            self.upload_manager(sorted_albums, queue_positions),
            self.send_manager(sorted_albums, queue_positions),
        )

    async def download_manager(self, sorted_albums, queue_positions):
        albums_dict = {album.grouped_id: album for album in sorted_albums}
        download_workers = {}
        while self.download_queue:
            ativos = [gid for gid in list(self.download_queue)[:self.max_download_queue]]
            # Inicie downloads para álbuns ativos que não estejam baixando ainda
            for gid in ativos:
                album = albums_dict[gid]
                pos = queue_positions[gid]
                if not pos.download_started and not pos.download_completed:
                    worker = asyncio.create_task(self.download_worker(album, pos))
                    download_workers[gid] = worker
                    pos.download_started = True
            # Tente promover o PRIMEIRO da fila para upload SE estiver baixado e houver vaga na fila de upload
            if len(self.upload_queue) < self.max_upload_queue and self.download_queue:
                first_gid = self.download_queue[0]
                pos = queue_positions[first_gid]
                album = albums_dict[first_gid]
                if pos.download_completed and all(m.downloaded for m in album.medias):
                    # Promover para upload!
                    self.upload_queue.append(self.download_queue.popleft())
                    self.logger.info(f"[PIPELINE] Álbum {first_gid} promovido para fila de upload (posição {pos.original_index})")
            # Se todos downloads e promoções feitos, pare
            if not self.download_queue and all(w.done() for w in download_workers.values()):
                break
            await asyncio.sleep(0.25)

    async def download_worker(self, album: AlbumInfo, position: QueuePosition):
        try:
            await self.download_album_safe(album)
            album.downloaded = True
            await self.progress_tracker.save_album(album)
            position.download_completed = True
            self.logger.info(f"[DOWNLOAD] Concluído álbum {album.grouped_id} (posição {position.original_index})")
        except Exception as e:
            self.logger.error(f"[DOWNLOAD] Erro no álbum {album.grouped_id}: {e}")

    async def upload_manager(self, sorted_albums, queue_positions):
        albums_dict = {album.grouped_id: album for album in sorted_albums}
        upload_workers = {}
        total_albuns = len(sorted_albums)
        while True:
            # Só processa o PRIMEIRO da fila de upload (nunca promove outros!)
            if self.upload_queue:
                gid = self.upload_queue[0]
                album = albums_dict[gid]
                pos = queue_positions[gid]
                if not pos.upload_started and not pos.upload_completed and gid not in upload_workers:
                    self.logger.info(f"[UPLOAD] Iniciando álbum {gid} (posição {pos.original_index})")
                    worker = asyncio.create_task(self.upload_worker(album, pos))
                    upload_workers[gid] = worker
                    pos.upload_started = True
                # Só promove para envio se o PRIMEIRO da fila terminou e foi realmente upado
                if len(self.send_queue) < 1:
                    if pos.upload_completed and album.uploaded:
                        self.send_queue.append(self.upload_queue.popleft())
                        self.logger.info(f"[PIPELINE] Álbum {gid} promovido para fila de envio (posição {pos.original_index})")
            # Limpa workers encerrados
            upload_workers = {gid: w for gid, w in upload_workers.items() if not w.done()}
            # Parada
            all_completed = sum(1 for pos in queue_positions.values() if pos.upload_completed)
            if all_completed == total_albuns:
                self.logger.info("[UPLOAD_MANAGER] Todos os álbuns upados. Encerrando upload_manager.")
                break
            await asyncio.sleep(0.25)

    async def upload_worker(self, album: AlbumInfo, position: QueuePosition):
        try:
            await self.upload_album_corrected(album)
            album.uploaded = True
            await self.progress_tracker.save_album(album)
            position.upload_completed = True
            self.logger.info(f"[UPLOAD] Concluído álbum {album.grouped_id} (posição {position.original_index})")
        except Exception as e:
            self.logger.error(f"[UPLOAD] Erro no álbum {album.grouped_id}: {e}")

    async def send_manager(self, sorted_albums, queue_positions):
        albums_dict = {album.grouped_id: album for album in sorted_albums}
        send_workers = {}
        total_albuns = len(sorted_albums)
        while True:
            # Só processa o PRIMEIRO da fila de envio (nunca promove outros!)
            if self.send_queue:
                gid = self.send_queue[0]
                album = albums_dict[gid]
                pos = queue_positions[gid]
                if not pos.send_completed and gid not in send_workers:
                    # REMOVIDO: verificação de intervalo (agora está no upload_album_corrected)
                    self.logger.info(f"[SEND] Iniciando álbum {gid} (posição {pos.original_index})")
                    worker = asyncio.create_task(self.send_worker(album, pos))
                    send_workers[gid] = worker
            # Remove workers finalizados e retire da fila de envio se já enviou
            for gid, worker in list(send_workers.items()):
                if worker.done():
                    self.logger.info(f"[SEND_MANAGER] Álbum {gid} enviado e removido da fila de envio")
                    self.send_queue.popleft()
                    del send_workers[gid]
            all_completed = sum(1 for pos in queue_positions.values() if pos.send_completed)
            if all_completed == total_albuns:
                self.logger.info("[SEND_MANAGER] Todos os álbuns enviados. Encerrando send_manager.")
                break
            await asyncio.sleep(0.25)
            
    async def send_worker(self, album: AlbumInfo, position: QueuePosition):
        try:
            # Marcar como enviado no banco ANTES de limpar os arquivos
            album.sent = True
            await self.progress_tracker.save_album(album)
            
            position.send_completed = True
            self.logger.info(f"[ENVIO] Concluído álbum {album.grouped_id} (posição {position.original_index})")
            
            # Limpar arquivos após marcar como enviado
            await self.cleanup_album_files(album)
        except Exception as e:
            self.logger.error(f"[ENVIO] Erro no álbum {album.grouped_id}: {e}")

    async def extract_media_info_safe(self, message: Message) -> Optional[MediaInfo]:
        for attempt in range(self.max_retries):
            try:
                return await asyncio.wait_for(
                    self.extract_media_info(message),
                    timeout=self.timeout
                )
            except asyncio.TimeoutError:
                self.logger.warning(f"Timeout extraindo mídia da mensagem {message.id}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(1)
                else:
                    return None
            except Exception as e:
                self.logger.warning(f"Erro extraindo mídia da mensagem {message.id}: {e}")
                return None
        return None

    async def extract_media_info(self, message: Message) -> Optional[MediaInfo]:
        if not hasattr(message, 'media') or not message.media:
            return None
        media_type = "unknown"
        file_size = 0
        file_name = f"media_{message.id}"
        try:
            if isinstance(message.media, MessageMediaPhoto):
                media_type = "photo"
                if hasattr(message.media.photo, 'sizes'):
                    largest_size = max(
                        message.media.photo.sizes,
                        key=lambda s: getattr(s, 'size', 0) if hasattr(s, 'size') else 0
                    )
                    file_size = getattr(largest_size, 'size', 0)
                file_name = f"photo_{message.id}.jpg"
            elif isinstance(message.media, MessageMediaDocument):
                doc = message.media.document
                file_size = getattr(doc, 'size', 0)
                if hasattr(doc, 'attributes'):
                    for attr in doc.attributes:
                        if hasattr(attr, 'file_name') and attr.file_name:
                            file_name = attr.file_name
                            break
                        elif hasattr(attr, 'performer') and hasattr(attr, 'title'):
                            file_name = f"{attr.performer} - {attr.title}.mp3"
                            media_type = "audio"
                            break
                    else:
                        if doc.mime_type:
                            if doc.mime_type.startswith('video/'):
                                media_type = "video"
                                file_name = f"video_{message.id}.mp4"
                            elif doc.mime_type.startswith('audio/'):
                                media_type = "audio"
                                file_name = f"audio_{message.id}.mp3"
                            elif doc.mime_type.startswith('image/'):
                                media_type = "photo"
                                file_name = f"image_{message.id}.jpg"
                            else:
                                media_type = "document"
                                file_name = f"doc_{message.id}"
                else:
                    media_type = "document"
                    file_name = f"doc_{message.id}"
        except Exception as e:
            self.logger.warning(f"Erro processando atributos da mídia: {e}")

        return MediaInfo(
            message_id=message.id,
            grouped_id=getattr(message, 'grouped_id', None),
            date=message.date,
            media_type=media_type,
            file_size=file_size,
            file_name=file_name,
            caption=getattr(message, 'message', None)
        )

    async def download_album_safe(self, album: AlbumInfo):
        self.logger.info(f"[DOWNLOAD] Iniciando álbum {album.grouped_id} ({len(album.medias)} mídias)")
        for i, media in enumerate(album.medias):
            if media.downloaded:
                continue
            file_path = self.temp_dir / f"{album.grouped_id}_{i}_{media.file_name}"
            for attempt in range(self.max_retries):
                try:
                    message = await self.safe_telegram_call(
                        self.reader_client,
                        self.reader_client.get_messages,
                        self.source_chat_id,
                        ids=media.message_id
                    )
                    if not message or not hasattr(message, 'media'):
                        self.logger.warning(f"Mensagem {media.message_id} não encontrada ou sem mídia")
                        break
                    await asyncio.sleep(self.download_delay)
                    downloaded_file = await self.safe_telegram_call(
                        self.reader_client,
                        self.reader_client.download_media,
                        message.media,
                        file=str(file_path)
                    )
                    if downloaded_file and os.path.exists(downloaded_file):
                        media.local_path = downloaded_file
                        media.downloaded = True
                        self.logger.info(f"[DOWNLOAD] Mídia {i+1}/{len(album.medias)} baixada: {media.file_name}")
                        break
                    else:
                        raise Exception("Arquivo não foi baixado corretamente")
                except Exception as e:
                    self.logger.warning(f"[DOWNLOAD] Tentativa {attempt+1} falhou para {media.file_name}: {e}")
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                    else:
                        self.logger.error(f"[DOWNLOAD] Falha definitiva para {media.file_name}")
                        raise

    async def upload_album_corrected(self, album: AlbumInfo):
        self.logger.info(f"[UPLOAD] Iniciando álbum {album.grouped_id}")
        
        # CORREÇÃO: Verificar intervalo de 1 hora no modo lento ANTES de enviar
        if self.slow_mode:
            current_time = time.time()
            time_since_last = current_time - self.last_send_time
            if self.last_send_time > 0 and time_since_last < self.send_interval:
                wait_time = self.send_interval - time_since_last
                self.logger.info(f"[MODO LENTO] Aguardando {wait_time/60:.1f} minutos até próximo envio...")
                await asyncio.sleep(wait_time)
        
        # Rate limit normal do Telegram
        current_time = time.time()
        time_since_last = current_time - self.last_upload_time
        if time_since_last < self.upload_delay:
            wait_time = self.upload_delay - time_since_last
            self.logger.info(f"[UPLOAD] Aguardando rate limit: {wait_time:.1f}s")
            await asyncio.sleep(wait_time)
        
        try:
            media_files = []
            for media in album.medias:
                if not media.local_path or not os.path.exists(media.local_path):
                    raise Exception(f"Arquivo local não encontrado: {media.file_name}")
                media_files.append(media.local_path)
            caption = album.caption if album.caption else None
            # Usando sender_client para enviar
            await self.safe_telegram_call(
                self.sender_client,
                self.sender_client.send_file,
                self.target_chat_id,
                media_files,
                caption=caption,
                force_document=False
            )
            self.last_upload_time = time.time()
            
            # IMPORTANTE: Atualizar timestamp do último envio no modo lento
            if self.slow_mode:
                self.last_send_time = time.time()
            
            self.logger.info(f"[UPLOAD] Álbum {album.grouped_id} enviado com sucesso pela conta de envio")
        except Exception as e:
            self.logger.error(f"[UPLOAD] Erro enviando álbum {album.grouped_id}: {e}")
            raise

    async def cleanup_album_files(self, album: AlbumInfo):
        for media in album.medias:
            if media.local_path and os.path.exists(media.local_path):
                try:
                    os.remove(media.local_path)
                    self.logger.debug(f"Arquivo removido: {media.local_path}")
                except Exception as e:
                    self.logger.warning(f"Erro removendo arquivo {media.local_path}: {e}")

    async def cleanup(self):
        try:
            if self.temp_dir.exists():
                shutil.rmtree(self.temp_dir)
                self.logger.info("Diretório temporário removido")
        except Exception as e:
            self.logger.warning(f"Erro na limpeza final: {e}")
        
        # Desconectar ambos os clientes
        if self.reader_client.is_connected():
            await self.reader_client.disconnect()
            self.logger.info("Cliente de leitura desconectado")
        
        if self.sender_client.is_connected():
            await self.sender_client.disconnect()
            self.logger.info("Cliente de envio desconectado")

async def main():
    # CREDENCIAIS DA CONTA DE LEITURA (conta atual)
    READER_API_ID = 12345678   # DIGITE A MESMA API ID AQUI
    READER_API_HASH = "DIGITE SUA API HASH AQUI"       # COLOQUE APENAS A MESMA API ID E API HASH NOS DOIS (CRIEI PARA CLONAR DE UMA CONTA PRA OUTRA)
    READER_SESSION_NAME = "album_transfer_reader_session"
    
    # CREDENCIAIS DA CONTA DE ENVIO (nova conta)
    SENDER_API_ID = 12345678     # DIGITE A MESMA API ID AQUI
    SENDER_API_HASH = "DIGITE A SUA API HASH AQUI"
    SENDER_SESSION_NAME = "album_transfer_sender_session"
    
    # IDs dos chats
    SOURCE_CHAT_ID = -1002519203567  # Grupo de origem (leitura)         #INSIRA AQUI O ID DO GRUPO QUE SERÁ CLONADO
    TARGET_CHAT_ID = -1002603926886  # Grupo de destino (envio)          #INSIRA AQUI O ID DO GRUPO QUE RECEBERÁ AS MENSAGENS
    
    # NOVO: Perguntar ao usuário qual modo usar
    print("\n" + "="*60)
    print("ESCOLHA O MODO DE TRANSFERÊNCIA:")
    print("="*60)
    print("1. MODO RÁPIDO - Transfere os álbuns sem intervalo (padrão)")
    print("2. MODO LENTO - Transfere 1 álbum a cada 1 hora")
    print("="*60)
    
    while True:
        choice = input("\nEscolha uma opção (1 ou 2): ").strip()
        if choice == "1":
            slow_mode = False
            print("\n✓ MODO RÁPIDO selecionado - Transferência sem intervalo")
            break
        elif choice == "2":
            slow_mode = True
            print("\n✓ MODO LENTO selecionado - 1 álbum a cada 1 hora")
            break
        else:
            print("❌ Opção inválida! Digite 1 ou 2.")
    
    print("\nIniciando transferência...")
    print("="*60 + "\n")
    
    transfer = TelegramAlbumTransfer(
        # Credenciais da conta de leitura
        reader_api_id=READER_API_ID,
        reader_api_hash=READER_API_HASH,
        reader_session_name=READER_SESSION_NAME,
        # Credenciais da conta de envio
        sender_api_id=SENDER_API_ID,
        sender_api_hash=SENDER_API_HASH,
        sender_session_name=SENDER_SESSION_NAME,
        # IDs dos chats
        source_chat_id=SOURCE_CHAT_ID,
        target_chat_id=TARGET_CHAT_ID,
        # Outras configurações
        max_download_queue=10,
        max_upload_queue=10,
        temp_dir="./temp_mediaErosFree",
        progress_db="./transfer_progress_ErosFree.db",
        slow_mode=slow_mode  # NOVO: passa o modo escolhido
    )
    try:
        await transfer.start()
    except KeyboardInterrupt:
        logging.info("Transferência interrompida pelo usuário")
    except Exception as e:
        logging.error(f"Erro na transferência: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())