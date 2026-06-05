# Docker + Ansible + PostgreSQL + Python — Полное руководство

---

## Что такое Docker и зачем он нужен
> 📎 [Docker — Getting Started](https://docs.docker.com/get-started/)

Представь что ты пишешь программу на своём компьютере. Она работает. Ты отправляешь её другу — у него не работает. Почему? Потому что у него другая версия Python, другая ОС, другие библиотеки.

**Docker решает эту проблему.** Ты упаковываешь программу вместе со всем что ей нужно (Python, библиотеки, конфиг) в один файл — **образ (image)**. Из образа запускается **контейнер** — изолированный процесс который работает одинаково везде.

### Основные понятия

- **Образ (image)** — готовый пакет с программой и всем что ей нужно. Собирается из Dockerfile.
- **Контейнер** — запущенный образ. Можно запустить несколько контейнеров из одного образа.
- **Dockerfile** — рецепт для сборки образа, пошаговые инструкции.
- **Docker Compose** — инструмент для запуска нескольких контейнеров вместе.
- **Volume** — папка вне контейнера для постоянного хранения данных.
- **Network** — виртуальная сеть внутри которой контейнеры видят друг друга по именам.

---

## Как app/db совмещаются с Ansible master/slave

Это частый вопрос. Вот простой ответ:

`app/docker-compose.yml` и `database/docker-compose.yml` — это два **разных** файла с разным содержимым. Разница между локальным запуском и Ansible только в том **где** они запускаются — файлы остаются теми же самыми.

**Локально (для разработки)** — ты запускаешь их напрямую на своём компьютере:
```
docker compose -f database/docker-compose.yml up -d
docker compose -f app/docker-compose.yml up -d
```

**Через Ansible (как в production)** — master заходит на slave серверы по SSH и запускает те же самые файлы, но уже на них:
```
ansible-slave-1 — отдельный "сервер" (контейнер)
    → Ansible клонирует твой репозиторий на него
    → Ansible запускает: docker compose -f app/docker-compose.yml up -d
    → Теперь app работает НА slave-1

ansible-slave-2 — второй "сервер"
    → Ansible клонирует репозиторий
    → Ansible запускает: docker compose -f database/docker-compose.yml up -d
    → Теперь db работает НА slave-2
```

**Правильный порядок разработки:**
1. Сначала пишешь app и db — разрабатываешь локально, тестируешь что всё работает
2. Потом создаёшь Ansible — когда app и db готовы, описываешь как их деплоить на серверы

Смысл такой: сначала делаешь рабочий продукт, потом автоматизируешь его развёртывание.

---

## Как работает проект — полная картина

```
Хост (твой компьютер)
│
├── database/docker-compose.yml
│   ├── postgres-db (PostgreSQL, порт 5432)
│   └── pgadmin_gui (pgAdmin, порт 5050)
│   └── сеть: my-network
│
├── app/docker-compose.yml
│   └── ansible-python-app (Flask, порт 5000)
│   └── подключается к сети: my-network → находит postgres-db
│
└── ansible/docker-compose.yml
    ├── ansible-master
    │   └── запускает playbook → SSH → slave-1, slave-2
    ├── ansible-slave-1 (app сервер)
    │   └── клонирует репо → docker compose up -d (app)
    └── ansible-slave-2 (db сервер)
        └── клонирует репо → docker compose up -d (database)
```

---

## Разбор файлов проекта

---

### `ansible-master/Dockerfile`
> 📎 [Dockerfile reference](https://docs.docker.com/reference/dockerfile/)

```dockerfile
FROM ubuntu:latest
```
Берём чистую Ubuntu — master это сервер который управляет остальными, нам нужна полноценная ОС.

```dockerfile
ENV DEBIAN_FRONTEND=noninteractive
```
Говорим apt не задавать вопросов при установке — иначе `docker build` зависнет ожидая ответа в терминале.

```dockerfile
RUN apt-get update && apt-get install -y \
    curl vim git openssh-server ansible nano sudo python3 \
    && rm -rf /var/lib/apt/lists/*
```
- `openssh-server` — нужен чтобы можно было зайти внутрь контейнера через `docker exec`
- `ansible` — главный инструмент, запускает playbook на slave серверах по SSH
- `python3` — Ansible написан на Python, требует его для работы
- `git` — нужен Ansible для работы с модулем `git` в playbook
- `rm -rf /var/lib/apt/lists/*` — удаляем кеш apt чтобы уменьшить размер образа

```dockerfile
RUN mkdir -p /var/run/sshd
```
SSH демон требует эту папку для своего PID файла — без неё не запустится. `-p` создаёт все промежуточные папки если не существуют.

```dockerfile
RUN useradd -m -s /bin/bash ansible && \
    echo 'ansible:123' | chpasswd && \
    echo 'ansible ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers
```
Создаём пользователя `ansible` от которого будем запускать playbook. Пароль `123` совпадает с паролем в `ansible-slave/Dockerfile` — один и тот же пользователь на всех серверах. `NOPASSWD:ALL` — пользователь может выполнять sudo без пароля. Смотри `ansible/inventory.ini` — там написано `ansible_user=ansible`.

```dockerfile
RUN mkdir -p /home/ansible/.ssh && \
    chown ansible:ansible /home/ansible/.ssh && \
    chmod 700 /home/ansible/.ssh
```
Создаём папку для SSH ключей пользователя ansible. `chmod 700` обязателен — SSH откажет если папка доступна другим пользователям.

```dockerfile
RUN ssh-keygen -t rsa -f /home/ansible/.ssh/id_rsa -N ""
```
Генерируем пару SSH ключей:
- `id_rsa` — приватный ключ, остаётся у master, используется для подключения к slave
- `id_rsa.pub` — публичный ключ, копируется на slave через shared volume (смотри `entrypoint.sh`)
- `-N ""` — пустой passphrase, иначе при каждом подключении SSH будет спрашивать пароль и playbook зависнет

```dockerfile
RUN chown -R ansible:ansible /home/ansible/.ssh && \
    chmod 600 /home/ansible/.ssh/id_rsa
```
`chmod 600` на приватный ключ обязателен — SSH намеренно отказывает если приватный ключ доступен кому-то кроме владельца. Это защита от утечки ключа.

```dockerfile
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
CMD ["/entrypoint.sh"]
```
При старте контейнера запускается `entrypoint.sh` — он копирует публичный ключ в shared volume и запускает SSH сервер.

---

### `ansible-master/entrypoint.sh`
> 📎 [sshd man page](https://www.man7.org/linux/man-pages/man8/sshd.8.html)

```bash
#!/bin/bash
```
Указывает что скрипт выполняется через bash.

```bash
cp /home/ansible/.ssh/id_rsa.pub /shared/authorized_keys
```
Копируем публичный ключ master в папку `/shared`. Эта папка — shared volume из `ansible/docker-compose.yml` который видят и master и оба slave. Slave при старте читает этот файл и кладёт его в свой `authorized_keys` — смотри `ansible-slave/entrypoint.sh`.

```bash
exec /usr/sbin/sshd -D
```
Запускаем SSH сервер. `-D` — foreground режим, не уходит в background. `exec` заменяет текущий процесс — Docker отслеживает именно его и знает когда контейнер завершился.

---

### `ansible-slave/Dockerfile`
> 📎 [Dockerfile reference](https://docs.docker.com/reference/dockerfile/)

```dockerfile
FROM ubuntu:latest
```
Берём чистую Ubuntu — slave это просто сервер, нам нужна минимальная ОС на которой устанавливаем всё остальное.

```dockerfile
ENV DEBIAN_FRONTEND=noninteractive
```
Говорим apt не задавать вопросов при установке — иначе `docker build` зависнет.

```dockerfile
RUN apt-get update && apt-get install -y \
    openssh-server git docker.io docker-compose \
    && apt-get -y autoremove && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
```
- `openssh-server` — нужен чтобы master мог подключиться к slave по SSH
- `git` — нужен чтобы склонировать репозиторий с GitHub когда Ansible запустит playbook
- `docker.io` — Docker Engine, нужен чтобы slave мог запускать контейнеры
- `docker-compose` — нужен чтобы slave мог выполнить `docker compose up -d`
- `autoremove` и `clean` — убираем ненужные зависимости и кеш, уменьшаем размер образа

```dockerfile
RUN mkdir -p /var/run/sshd
```
SSH демон не запустится без этой папки — он пишет туда свой PID файл.

```dockerfile
RUN useradd -m -s /bin/bash ansible && \
    echo 'ansible:123' | chpasswd && \
    echo 'ansible ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers
```
Создаём пользователя `ansible` — именно от этого пользователя master будет подключаться по SSH. Смотри `ansible/inventory.ini` — там написано `ansible_user=ansible`.

```dockerfile
RUN mkdir -p /home/ansible/.ssh && \
    chown ansible:ansible /home/ansible/.ssh && \
    chmod 700 /home/ansible/.ssh
```
Создаём папку для SSH ключей. `chmod 700` обязателен — SSH откажет если папка доступна другим пользователям.

```dockerfile
RUN usermod -aG docker ansible
```
Добавляем пользователя ansible в группу docker — без этого ansible не может запускать docker команды. Но из-за разных GID на хосте этого недостаточно — смотри `entrypoint.sh` где делаем `chmod 666`.

```dockerfile
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
CMD ["/entrypoint.sh"]
```
При старте контейнера запускается `entrypoint.sh` — он настраивает SSH ключи и запускает SSH сервер.

---

### `ansible-slave/entrypoint.sh`
> 📎 [Docker volumes](https://docs.docker.com/storage/volumes/) · [Docker socket](https://docs.docker.com/engine/install/linux-postinstall/)

```bash
#!/bin/bash
```
Указывает что скрипт выполняется через bash.

```bash
chmod 666 /var/run/docker.sock
```
Docker socket монтируется с хоста через `ansible/docker-compose.yml`. Группа `docker` внутри контейнера имеет другой GID чем на хосте — поэтому даже добавление ansible в группу docker не помогает. `chmod 666` открывает socket для всех пользователей.

```bash
cp /shared/authorized_keys /home/ansible/.ssh/authorized_keys
chown ansible:ansible /home/ansible/.ssh/authorized_keys
chmod 600 /home/ansible/.ssh/authorized_keys
```
Копируем публичный ключ master из shared volume в правильное место. SSH читает `authorized_keys` и пускает того чей ключ там есть — то есть master. `chmod 600` обязателен — SSH откажет если права шире.

**Почему не монтировать volume напрямую в `authorized_keys`?** Docker создаёт папку вместо файла если монтируешь volume в несуществующий путь — SSH ожидает файл и отказывает.

```bash
exec /usr/sbin/sshd -D
```
Запускаем SSH сервер — master будет подключаться к slave именно через него.

---

### `ansible/docker-compose.yml`
> 📎 [Compose file reference](https://docs.docker.com/compose/compose-file/)

```yaml
ansible-master:
  image: igorvakul/ansible-master:latest
```
Берём образ с DockerHub который мы собрали командой `docker build` и запушили командой `docker push`.

```yaml
    volumes:
      - shared_keys:/shared
      - ./:/ansible-project
```
- `shared_keys:/shared` — shared volume, master пишет сюда публичный ключ. Тот же volume у обоих slaves — они читают ключ отсюда в своём `entrypoint.sh`
- `./:/ansible-project` — монтируем текущую папку `ansible/` внутрь контейнера. Так `inventory.ini` и `deploy.yml` видны внутри master без копирования в образ

```yaml
  ansible-slave-1:
    volumes:
      - shared_keys:/shared
      - /var/run/docker.sock:/var/run/docker.sock
```
- `shared_keys:/shared` — тот же volume что у master, slave читает публичный ключ отсюда в `entrypoint.sh`
- `/var/run/docker.sock:/var/run/docker.sock` — монтируем Docker socket с хоста. Без этого slave не может запускать `docker compose up -d`

```yaml
volumes:
  shared_keys:
```
Объявляем named volume — Docker создаёт и управляет им сам. Живёт пока не удалишь командой `docker compose down -v`.

```yaml
networks:
  ansible-network:
    driver: bridge
```
Все три контейнера в одной сети — могут обращаться друг к другу по именам `ansible-slave-1`, `ansible-slave-2`. Ansible подключается к slave именно по этим именам из `inventory.ini`.

---

### `ansible/inventory.ini`
> 📎 [Ansible inventory](https://docs.ansible.com/ansible/latest/inventory_guide/intro_inventory.html)

```ini
[webservers]
ansible-slave-1 ansible_user=ansible
```
- `[webservers]` — группа серверов. В `deploy.yml` пишем `hosts: webservers` — Ansible берёт все серверы из этой группы
- `ansible-slave-1` — имя хоста. Работает потому что все в одной сети `ansible-network` из `ansible/docker-compose.yml`
- `ansible_user=ansible` — подключаться от пользователя `ansible`. Этот пользователь создан в `ansible-slave/Dockerfile`

```ini
[dbservers]
ansible-slave-2 ansible_user=ansible
```
Вторая группа — database сервер. В `deploy.yml` второй play запускается именно на этой группе.

```ini
[all:vars]
ansible_ssh_common_args='-o StrictHostKeyChecking=no'
```
При первом SSH подключении система спрашивает "доверяешь ли ты этому серверу?". В автоматическом режиме некому ответить — playbook зависнет. Этот флаг говорит доверять всем серверам без вопросов.

---

### `ansible/deploy.yml`
> 📎 [Ansible playbooks](https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_intro.html) · [become/privilege escalation](https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_privilege_escalation.html)

```yaml
- hosts: webservers
```
Этот play выполняется только на серверах из группы `webservers` в `inventory.ini` — то есть на `ansible-slave-1`.

```yaml
    - name: Clone repository
      git:
        repo: https://github.com/Igor-Vakul/lh-mini-project-docker.git
        dest: /home/ansible/project
        force: yes
```
Ansible модуль `git` клонирует репозиторий на slave сервер в папку `/home/ansible/project`. `force: yes` — перезаписать если уже есть. Репозиторий нужен на slave потому что там лежат `docker-compose.yml` файлы которые slave будет запускать.

```yaml
    - name: Deploy Ansible Slave-1(Application) using Docker Compose
      shell: docker compose up -d
      args:
        chdir: /home/ansible/project/app
```
`shell` модуль выполняет команду на slave. `chdir` — перейти в папку `/home/ansible/project/app` перед выполнением — именно там лежит `app/docker-compose.yml` который запустит Python приложение.

```yaml
- hosts: dbservers
```
Второй play — выполняется на `ansible-slave-2` из группы `dbservers`. Те же шаги но `chdir` ведёт в `/home/ansible/project/database` — запускает PostgreSQL + pgAdmin.

---

### UFW — файервол (Бонус)
> 📎 [community.general.ufw module](https://docs.ansible.com/ansible/latest/collections/community/general/ufw_module.html) · [UFW Ubuntu docs](https://help.ubuntu.com/community/UFW)

**Что такое UFW?**
UFW (Uncomplicated Firewall) — это файервол, управляет какие сетевые подключения разрешены на сервере. По умолчанию на Linux всё открыто — любой порт доступен кому угодно. UFW позволяет закрыть всё и открыть только нужное.

**Как UFW работает под капотом:**
UFW — обёртка над `iptables`. `iptables` — это инструмент ядра Linux который фильтрует пакеты. Поэтому UFW требует доступа к ядру — в Docker контейнерах это не работает по умолчанию.

**Почему нужен `cap_add` в docker-compose.yml:**
```yaml
cap_add:
  - NET_ADMIN
  - NET_RAW
```
Docker запускает контейнеры с ограниченными правами в целях безопасности. `NET_ADMIN` даёт право управлять сетевыми настройками (iptables, UFW). `NET_RAW` даёт право работать с сырыми сетевыми пакетами. Без этих прав UFW выдаёт ошибку `Permission denied`.

---

**Разбор UFW play в `deploy.yml`:**

```yaml
- name: Configure UFW
  hosts: all
  become: true
```
`become: true` на уровне play — все задачи выполняются от root через sudo. Это обязательно для UFW — он требует прав root. `hosts: all` — применяется и к slave-1 и к slave-2.

```yaml
    - name: Deny incoming connections by default
      community.general.ufw:
        direction: incoming
        policy: deny
```
Закрываем все входящие подключения по умолчанию. После этого ничто извне не может подключиться к серверу — ни по каким портам. Следующие задачи будут открывать конкретные нужные порты.

```yaml
    - name: Allow outgoing connections by default
      community.general.ufw:
        direction: outgoing
        policy: allow
```
Исходящие подключения открыты — сервер может сам обращаться наружу (например клонировать репозиторий с GitHub).

```yaml
    - name: Allow SSH port
      community.general.ufw:
        rule: allow
        port: '22'
        proto: tcp
```
Открываем порт 22 — это порт SSH. Это нужно сделать ДО включения UFW, иначе Ansible потеряет SSH соединение и playbook зависнет.

```yaml
    - name: Enable UFW
      community.general.ufw:
        state: enabled
```
Включаем UFW — с этого момента правила начинают действовать.

---

**Порты на webservers (slave-1 — приложение):**

```yaml
    - name: Allow port 5000
      community.general.ufw:
        rule: allow
        port: '5000'
        proto: tcp
```
Порт 5000 — Flask приложение. Открываем для всех (`src:` не указан) — пользователи должны иметь доступ к сайту.

**Итог на slave-1:**
- `22/tcp` — SSH (для Ansible и управления)
- `5000/tcp` — Flask приложение (для пользователей)

---

**Порты на dbservers (slave-2 — база данных):**

```yaml
    - name: Allow port 5432 only from slave-1
      community.general.ufw:
        rule: allow
        port: '5432'
        proto: tcp
        src: 172.28.0.10
```
Порт 5432 — PostgreSQL. `src: 172.28.0.10` — разрешаем только с IP адреса slave-1. База данных должна быть доступна только приложению, не внешнему миру. `172.28.0.10` — статический IP который мы назначили slave-1 в `docker-compose.yml`.

**Почему нужен статический IP для slave-1:**
Docker назначает IP динамически — при каждом перезапуске IP может измениться. Поэтому мы заранее назначаем фиксированный IP через `ipv4_address` в `docker-compose.yml`. Без статического IP мы не знаем какой `src:` писать в правиле UFW.

**Итог на slave-2:**
- `22/tcp` — SSH (для Ansible)
- `5432/tcp` от `172.28.0.10` — PostgreSQL только от slave-1

---

**Как проверить что UFW работает:**
```bash
# Зайти в slave-1
docker exec -it ansible-slave-1 bash
ufw status

# Зайти в slave-2
docker exec -it ansible-slave-2 bash
ufw status
```

Ожидаемый результат на slave-1:
```
Status: active
22/tcp    ALLOW  Anywhere
5000/tcp  ALLOW  Anywhere
```

Ожидаемый результат на slave-2:
```
Status: active
22/tcp    ALLOW  Anywhere
5432/tcp  ALLOW  172.28.0.10
```

---

**Статические IP в `ansible/docker-compose.yml`:**
```yaml
ansible-slave-1:
  networks:
    ansible-network:
      ipv4_address: 172.28.0.10
```
Формат словаря (не список с `-`) — обязателен когда нужно задать дополнительные параметры сети. `ipv4_address` требует чтобы в разделе `networks` была задана подсеть `subnet`.

```yaml
networks:
  ansible-network:
    driver: bridge
    ipam:
      config:
        - subnet: 172.28.0.0/16
```
`ipam` — IP Address Management. `subnet: 172.28.0.0/16` — диапазон адресов для этой сети. `/16` означает что первые два октета фиксированы (`172.28`), третий и четвёртый свободны. Подсеть должна быть уникальной — не пересекаться с другими Docker сетями на машине (проверить: `docker network ls`).

---

### `database/docker-compose.yml`
> 📎 [postgres — Docker Hub](https://hub.docker.com/_/postgres) · [pgAdmin — Docker Hub](https://hub.docker.com/r/dpage/pgadmin4)

```yaml
db:
  image: postgres:16
  container_name: postgres-db
```
Фиксируем версию `postgres:16` — `latest` тянет PostgreSQL 18 у которого изменился путь для данных и контейнер падает. `container_name: postgres-db` — по этому имени приложение находит базу данных. Смотри `app/docker-compose.yml` строку `DB_HOST=postgres-db`.

```yaml
    environment:
      POSTGRES_USER: root
      POSTGRES_PASSWORD: 1
      POSTGRES_DB: mydb
```
Эти переменные должны совпадать с переменными в `app/docker-compose.yml` — `DB_USER=root`, `DB_PASSWORD=1`, `DB_NAME=mydb`. Иначе приложение не сможет подключиться к базе.

```yaml
    volumes:
      - postgres_data:/var/lib/postgresql/data
```
PostgreSQL пишет данные в `/var/lib/postgresql/data`. Без volume все данные исчезнут когда контейнер остановится.

```yaml
  pgadmin:
    image: dpage/pgadmin4:latest
    ports:
      - "5050:80"
```
pgAdmin — веб-интерфейс для управления базой. Открывается на `http://localhost:5050`. Внутри контейнера работает на порту `80`, снаружи доступен на `5050`.

```yaml
networks:
  my-network:
    name: my-network
    driver: bridge
```
`name: my-network` обязателен — без него Docker добавит префикс папки и сеть будет называться `database_my-network`. Тогда `app/docker-compose.yml` не найдёт её по имени `my-network`.

---

### `app/docker-compose.yml`
> 📎 [Compose — networking](https://docs.docker.com/compose/networking/) · [Compose — environment variables](https://docs.docker.com/compose/environment-variables/)

```yaml
    environment:
      - DB_HOST=postgres-db
      - DB_USER=root
      - DB_PASSWORD=1
      - DB_NAME=mydb
```
Эти переменные попадают в контейнер и читаются в `app.py` через `os.environ.get('DB_HOST')`. `DB_HOST=postgres-db` — имя контейнера базы данных. Docker DNS разрешает его в IP адрес внутри сети `my-network`.

```yaml
networks:
  my-network:
    external: true
```
Сеть создана в `database/docker-compose.yml`. `external: true` — не создавать новую, а подключиться к существующей. База данных должна быть запущена первой, иначе сеть не существует и app не запустится.

---

### `app/Dockerfile`
> 📎 [Dockerfile reference](https://docs.docker.com/reference/dockerfile/) · [Gunicorn docs](https://gunicorn.org/)

```dockerfile
FROM ubuntu:22.04
```
Ubuntu как основа — по условию задания.

```dockerfile
RUN apt-get update && apt-get install -y \
    python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*
```
Устанавливаем Python и pip — нужны для запуска Flask приложения и установки зависимостей.

```dockerfile
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
```
Сначала копируем только `requirements.txt` и устанавливаем зависимости. Docker кэширует этот слой — если `requirements.txt` не изменился, при следующей сборке зависимости не будут устанавливаться заново. Это экономит время сборки.

```dockerfile
COPY src/ .
```
Копируем только папку `src/` — там `app.py` и `templates/`. Остальные файлы (docker-compose.yml, Dockerfile) в контейнер не нужны.

```dockerfile
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "app:app"]
```
Запускаем через gunicorn а не `python app.py` — gunicorn это production сервер который обрабатывает несколько запросов одновременно. `app:app` — файл `app.py`, объект `app = Flask(__name__)`.

---

### `app/src/app.py`
> 📎 [Flask docs](https://flask.palletsprojects.com/) · [psycopg2 docs](https://www.psycopg.org/docs/)

```python
load_dotenv()
```
Загружает переменные из `.env` файла. При запуске через Docker этот файл не используется — переменные приходят из `docker-compose.yml`. Нужен только для локального запуска через `python app.py`.

```python
def get_db():
    return psycopg2.connect(
        host=os.environ.get('DB_HOST'),
        user=os.environ.get('DB_USER'),
        password=os.environ.get('DB_PASSWORD'),
        dbname=os.environ.get('DB_NAME'),
        port=os.environ.get('DB_PORT', 5432)
    )
```
Подключается к PostgreSQL используя переменные окружения из `app/docker-compose.yml`. `DB_HOST=postgres-db` — имя контейнера базы которое Docker DNS разрешает в IP внутри сети `my-network`.

```python
def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (...)
    """)
    conn.commit()

init_db()
```
Создаёт таблицу `users` если её нет. Вызывается на уровне модуля — выполняется при любом запуске включая gunicorn. Если убрать его из этого места и оставить только в `if __name__ == '__main__':` — gunicorn его не вызовет и таблица не создастся — все запросы упадут с ошибкой `relation "users" does not exist`.

```python
@app.route('/users', methods=['GET'])
def get_users():
```
Эндпоинт который вызывается когда фронтенд делает `fetch('/users')` в `templates/index.html`. Возвращает список всех пользователей из таблицы в JSON формате.

```python
@app.route('/users', methods=['POST'])
def create_user():
```
Принимает JSON с `username`, `email`, `password` и добавляет запись в таблицу. Возвращает созданного пользователя с его `id`.

```python
@app.route('/')
def index():
    return render_template('index.html')
```
Отдаёт HTML страницу. `templates/index.html` должен лежать в папке `templates/` рядом с `app.py` — Flask ищет шаблоны именно там.

---

### `app/src/templates/index.html`
> 📎 [Fetch API — MDN](https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API)

```javascript
async function loadUsers() {
    const res = await fetch('/users');
    const users = await res.json();
    const tbody = document.getElementById('users-table');
    tbody.innerHTML = users.map(u =>
        `<tr><td>${u.id}</td><td>${u.username}</td><td>${u.email}</td><td>${u.created_at}</td></tr>`
    ).join('');
}
```
Делает `GET /users` запрос к Flask приложению. Flask возвращает JSON со списком пользователей — JavaScript вставляет каждого в строку таблицы.

```javascript
document.getElementById('user-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    await fetch('/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({...})
    });
    e.target.reset();
    loadUsers();
});
```
`e.preventDefault()` — отменяет стандартное поведение формы (перезагрузку страницы). Отправляет `POST /users` с данными из формы. После успешного создания очищает форму и перезагружает список пользователей.

```javascript
loadUsers();
```
Вызывается сразу при открытии страницы — загружает существующих пользователей из базы данных.

---

## Список всех команд

### Сборка образов
```bash
docker build -t igorvakul/ansible-master:latest ./ansible-master
docker build -t igorvakul/ansible-slave:latest ./ansible-slave
docker push igorvakul/ansible-master:latest
docker push igorvakul/ansible-slave:latest
```

### База данных
```bash
# Запустить
docker compose -f database/docker-compose.yml up -d

# Остановить (сохранить данные)
docker compose -f database/docker-compose.yml down

# Остановить и удалить данные
docker compose -f database/docker-compose.yml down -v
```

### Приложение
```bash
# Запустить (с пересборкой образа)
docker compose -f app/docker-compose.yml up -d --build

# Остановить
docker compose -f app/docker-compose.yml down
```

### Ansible инфраструктура
```bash
# Полный перезапуск (обязательно down перед up если меняли образы)
docker compose -f ansible/docker-compose.yml down
docker compose -f ansible/docker-compose.yml up -d
```

### Запуск playbook
```bash
# Зайти в master
docker exec -it ansible-master bash

# Переключиться на пользователя ansible
su - ansible

# Перейти в папку с файлами
cd /ansible-project

# Запустить playbook
ansible-playbook -i inventory.ini deploy.yml
```

### Проверка логов
```bash
docker logs ansible-python-app
docker logs postgres-db
docker logs ansible-slave-1
```

### Проверка запущенных контейнеров
```bash
docker ps
```

### Git
```bash
git add .
git commit -m "message"
git push
```

### Полезные команды для отладки
```bash
# Зайти внутрь контейнера
docker exec -it <container_name> bash

# Посмотреть файлы внутри контейнера
ls -la /home/ansible/.ssh/

# Проверить содержимое файла
cat /home/ansible/.ssh/authorized_keys

# Проверить сети
docker network ls

# Проверить volumes
docker volume ls
```

---

## Порядок запуска проекта с нуля

Всегда запускать в этом порядке — каждый шаг зависит от предыдущего.

**1. База данных** (создаёт сеть `my-network`)
```bash
docker compose -f database/docker-compose.yml up -d
```

**2. Приложение** (подключается к сети `my-network` которую создала БД)
```bash
docker compose -f app/docker-compose.yml up -d --build
```

**3. Ansible инфраструктура**
```bash
docker compose -f ansible/docker-compose.yml up -d
```

**4. Запуск playbook** (клонирует репо на slaves, запускает docker compose на каждом, настраивает UFW)
```bash
docker exec -it ansible-master bash
su - ansible
cd /ansible-project
ansible-playbook -i inventory.ini deploy.yml
```

**5. Проверка**
- Сайт: `http://localhost:5000`
- pgAdmin: `http://localhost:5050`
- UFW на slave-1: `docker exec -it ansible-slave-1 bash` → `ufw status`
- UFW на slave-2: `docker exec -it ansible-slave-2 bash` → `ufw status`

---

## Docker Hub — сборка и публикация образов
> 📎 [Docker Hub docs](https://docs.docker.com/docker-hub/)

Docker Hub — это реестр образов (как GitHub но для Docker). Образы хранятся там чтобы любая машина могла их скачать командой `docker pull`.

**Зачем это нужно в проекте:**
`ansible/docker-compose.yml` использует `image: igorvakul/ansible-master:latest` — Docker скачивает этот образ с Docker Hub при запуске. Если образ не запушен — контейнер не запустится.

**Workflow:**
```bash
# 1. Собрать образ локально
docker build -t igorvakul/ansible-master:latest ./ansible-master
docker build -t igorvakul/ansible-slave:latest ./ansible-slave

# 2. Запушить на Docker Hub
docker push igorvakul/ansible-master:latest
docker push igorvakul/ansible-slave:latest
```

**Когда нужен ребилд и пуш:**
- Изменился `Dockerfile`
- Изменился `entrypoint.sh`
- Всё остальное (docker-compose.yml, deploy.yml, inventory.ini) — ребилд не нужен, файлы монтируются через volumes или клонируются с GitHub

---

## Git — ветки
> 📎 [Git branching](https://git-scm.com/book/en/v2/Git-Branching-Branches-in-a-Nutshell)

**Зачем нужны ветки:**
Ветки позволяют держать разные версии кода одновременно. Например: `main` — чистый код без комментариев, `with-comments` — тот же код с объяснениями.

```bash
# Создать новую ветку и переключиться на неё
git checkout -b with-comments

# Переключиться на существующую ветку
git checkout main

# Посмотреть все ветки
git branch

# Запушить ветку на GitHub
git push origin with-comments
```

**Как работает:**
- Каждая ветка — независимая копия кода
- Изменения в `with-comments` не затрагивают `main`
- Можно переключаться между ветками — файлы меняются автоматически

---

## Типичные ошибки проекта

### `postgres:latest` падает при старте
**Причина:** `latest` тянет PostgreSQL 18 у которого изменился путь для данных.
**Решение:** Зафиксировать версию `postgres:16` в `database/docker-compose.yml`.

---

### Сеть `my-network` не найдена
**Ошибка:** `network my-network declared as external, but could not be found`
**Причина:** Docker добавляет префикс папки к имени сети — `database_my-network` вместо `my-network`.
**Решение:** Добавить `name: my-network` в секцию networks в `database/docker-compose.yml`.

---

### `authorized_keys` создаётся как папка
**Причина:** Docker создаёт директорию вместо файла если монтировать volume напрямую в `authorized_keys`.
**Решение:** Монтировать volume в `/shared`, копировать файл в `entrypoint.sh`.

---

### Docker socket — Permission denied
**Ошибка:** `permission denied while trying to connect to Docker daemon socket`
**Причина:** GID группы `docker` внутри контейнера отличается от хоста — `usermod -aG docker ansible` не помогает.
**Решение:** Добавить `chmod 666 /var/run/docker.sock` в `ansible-slave/entrypoint.sh`.

---

### SSH ключ не совпадает после пересборки master
**Причина:** При `docker build` генерируется новая пара ключей — старый публичный ключ на slave больше не совпадает с новым приватным ключом на master.
**Решение:** После пересборки master сделать полный перезапуск: `docker compose down` → `docker compose up -d` чтобы entrypoint.sh скопировал новый ключ.

---

### `become: true` внутри параметров модуля
**Причина:** `become: true` написан с отступом внутри задачи вместо уровня play.
**Решение:** `become: true` должен быть на одном уровне с `tasks:` и `hosts:`.

```yaml
# Неправильно
- community.general.ufw:
    rule: allow
    become: true     # ← внутри модуля

# Правильно
- hosts: webservers
  become: true       # ← уровень play
  tasks:
    - community.general.ufw:
        rule: allow
```

---

### Subnet overlap при создании сети
**Ошибка:** `invalid pool request: Pool overlaps with other one on this address space`
**Причина:** Заданная подсеть уже используется другой Docker сетью на машине.
**Решение:** Проверить занятые подсети (`docker network inspect $(docker network ls -q) --format "{{.Name}}: {{range .IPAM.Config}}{{.Subnet}}{{end}}"`) и выбрать свободную.

---

## Неочевидные детали

### `force: yes` в git модуле
> 📎 [ansible.builtin.git module](https://docs.ansible.com/ansible/latest/collections/ansible/builtin/git_module.html)

```yaml
- name: Clone repository
  git:
    repo: https://github.com/...
    dest: /home/ansible/project
    force: yes
```

Без `force: yes` — если папка `/home/ansible/project` уже существует (например после второго запуска playbook), Ansible откажет с ошибкой. `force: yes` перезаписывает существующий репозиторий. Это позволяет запускать playbook повторно без ошибок.

---

### `-o StrictHostKeyChecking=no` в inventory.ini
> 📎 [SSH config options](https://www.ssh.com/academy/ssh/config)

```ini
[all:vars]
ansible_ssh_common_args='-o StrictHostKeyChecking=no'
```

При первом SSH подключении к новому серверу система спрашивает: "Ты доверяешь этому серверу? (yes/no)". В автоматическом режиме некому ответить — playbook зависает и ждёт вечно. Этот флаг говорит доверять всем серверам без вопросов. В production так делать не стоит — но для локальных контейнеров нормально.

---

### `exec` в entrypoint.sh
> 📎 [exec — Linux man page](https://www.man7.org/linux/man-pages/man3/exec.3.html)

```bash
exec /usr/sbin/sshd -D
```

Если написать просто `/usr/sbin/sshd -D` без `exec` — bash запустит sshd как дочерний процесс. Docker отслеживает PID 1 (первый процесс) — им будет bash. Когда bash завершится, Docker остановит контейнер, даже если sshd ещё работает.

`exec` заменяет текущий bash процесс на sshd — sshd становится PID 1. Docker теперь отслеживает именно его. Контейнер живёт пока живёт sshd.

---

### `--no-cache-dir` в pip
> 📎 [pip install docs](https://pip.pypa.io/en/stable/cli/pip_install/#cmdoption-no-cache-dir)

```dockerfile
RUN pip3 install --no-cache-dir -r requirements.txt
```

pip по умолчанию кэширует скачанные пакеты в `/root/.cache/pip`. В Docker этот кэш бесполезен — следующая сборка всё равно запустит pip заново (новый слой). `--no-cache-dir` не сохраняет кэш — образ получается меньше.

---

### `depends_on` в docker-compose
> 📎 [Compose depends_on](https://docs.docker.com/compose/compose-file/05-services/#depends_on)

```yaml
pgadmin:
  depends_on:
    - db
```

`depends_on` гарантирует только **порядок запуска** контейнеров — сначала `db`, потом `pgadmin`. Но не гарантирует что PostgreSQL **готов принимать подключения** к моменту старта pgadmin. PostgreSQL стартует несколько секунд. Если pgadmin подключится слишком быстро — получит ошибку. Для production используют `healthcheck`, для этого проекта `depends_on` достаточно.

---

### `restart: always`
> 📎 [Compose restart policy](https://docs.docker.com/compose/compose-file/05-services/#restart)

```yaml
restart: always
```

Контейнер автоматически перезапустится если:
- Упал с ошибкой
- Docker daemon перезапустился (например после перезагрузки компьютера)

Без `restart: always` — после перезагрузки компьютера все контейнеры остановлены и нужно запускать вручную.

---

### `-D` флаг у sshd
> 📎 [sshd man page](https://www.man7.org/linux/man-pages/man8/sshd.8.html)

```bash
/usr/sbin/sshd -D
```

По умолчанию sshd запускается в background режиме (daemon) — уходит в фон и возвращает управление терминалу. Для Docker это проблема: если entrypoint.sh завершается, Docker считает что контейнер завершил работу и останавливает его.

`-D` — foreground режим, sshd не уходит в фон. entrypoint.sh "зависает" на этой строке и держит контейнер живым пока работает sshd.

---

### Named volume vs bind mount
> 📎 [Docker storage overview](https://docs.docker.com/storage/)

В docker-compose используются два разных типа монтирования:

```yaml
volumes:
  - shared_keys:/shared        # named volume
  - ./:/ansible-project        # bind mount
  - /var/run/docker.sock:/var/run/docker.sock  # bind mount
```

**Named volume** (`shared_keys:/shared`) — Docker создаёт и управляет папкой сам, она живёт в системе Docker (`/var/lib/docker/volumes/`). Удаляется только через `docker compose down -v`. Используется когда данные нужно сохранять между перезапусками или шарить между контейнерами.

**Bind mount** (`./:/ansible-project`) — монтирует конкретную папку с хоста. `./` — текущая папка на хосте, `/ansible-project` — куда она появится внутри контейнера. Изменения на хосте сразу видны внутри контейнера. Используется когда нужно работать с файлами хоста (код, конфиги).

---

### Порт mapping `host:container`
> 📎 [Compose ports](https://docs.docker.com/compose/compose-file/05-services/#ports)

```yaml
ports:
  - "5000:5000"   # Flask
  - "5050:80"     # pgAdmin
  - "5432:5432"   # PostgreSQL
```

Формат: `порт_на_хосте:порт_внутри_контейнера`

- `5000:5000` — Flask внутри контейнера слушает порт 5000, снаружи тоже доступен на 5000. `http://localhost:5000`
- `5050:80` — pgAdmin внутри контейнера работает на порту 80 (стандартный HTTP), но снаружи доступен на 5050. Так сделано чтобы не занимать порт 80 на хосте. `http://localhost:5050`

Порт на хосте может быть любым свободным — он не обязан совпадать с портом контейнера.

---

### Ansible collections и `community.general`
> 📎 [Ansible collections guide](https://docs.ansible.com/ansible/latest/collections_guide/index.html) · [ansible-galaxy](https://docs.ansible.com/ansible/latest/galaxy/user_guide.html)

Ansible поставляется с базовым набором модулей (`ansible.builtin`). Дополнительные модули распространяются в виде **collections** — пакетов которые нужно установить отдельно.

`community.general` — коллекция от сообщества, содержит модуль `ufw` (и сотни других). Без неё Ansible не знает что такое `community.general.ufw` и выдаст ошибку.

```dockerfile
RUN ansible-galaxy collection install community.general
```

`ansible-galaxy` — менеджер пакетов для Ansible (как pip для Python). Устанавливается в Dockerfile чтобы коллекция была в образе — иначе пришлось бы устанавливать каждый раз при старте контейнера.

---

### `chdir` в shell модуле
> 📎 [ansible.builtin.shell module](https://docs.ansible.com/ansible/latest/collections/ansible/builtin/shell_module.html)

```yaml
- name: Deploy using Docker Compose
  shell: docker compose up -d
  args:
    chdir: /home/ansible/project/app
```

Почему не написать `cd /home/ansible/project/app && docker compose up -d`?

Технически можно, но `chdir` — правильный способ в Ansible. `shell` модуль каждый раз запускает новый shell процесс — переменная текущей директории не сохраняется между задачами. `chdir` гарантированно переходит в папку перед выполнением команды и это явно видно в коде.

---

### Python interpreter warning в выводе playbook
> 📎 [Ansible interpreter discovery](https://docs.ansible.com/ansible/latest/reference_appendices/interpreter_discovery.html)

```
[WARNING]: Host 'ansible-slave-1' is using the discovered Python interpreter
at '/usr/bin/python3.14'
```

Ansible запускает Python модули на slave серверах. Он автоматически ищет Python — нашёл `/usr/bin/python3.14`. Предупреждение говорит: "я нашёл Python сам, но если ты установишь другую версию — я могу найти не ту".

Это не ошибка — просто информация. Чтобы убрать предупреждение можно явно указать в `inventory.ini`:
```ini
[all:vars]
ansible_python_interpreter=/usr/bin/python3
```
Для этого проекта предупреждение не влияет на работу.

---

### `0.0.0.0` в gunicorn
> 📎 [Gunicorn configuration](https://docs.gunicorn.org/en/stable/settings.html#bind)

```dockerfile
CMD ["gunicorn", "--bind", "0.0.0.0:5000", ...]
```

`0.0.0.0` означает "слушать на всех сетевых интерфейсах". Если написать `127.0.0.1:5000` (localhost) — приложение будет доступно только внутри самого контейнера. Docker не сможет пробросить порт наружу, и `http://localhost:5000` на хосте не откроется.

`0.0.0.0` — обязательно для любого сервера внутри Docker контейнера который должен быть доступен снаружи.

---

### Docker image layers
> 📎 [Docker build cache](https://docs.docker.com/build/cache/)

Каждая инструкция `RUN`, `COPY`, `ADD` в Dockerfile создаёт новый **слой (layer)**. Docker кэширует слои — если инструкция не изменилась, при следующей сборке берётся кэш.

**Почему команды объединяют через `&&`:**
```dockerfile
# Плохо — 3 слоя, кэш apt остаётся в образе
RUN apt-get update
RUN apt-get install -y git
RUN rm -rf /var/lib/apt/lists/*

# Хорошо — 1 слой, кэш удаляется в том же слое
RUN apt-get update && apt-get install -y git \
    && rm -rf /var/lib/apt/lists/*
```

Если `rm -rf /var/lib/apt/lists/*` в отдельном `RUN` — кэш apt уже зафиксирован в предыдущем слое и в финальном образе всё равно занимает место. Только в одном `RUN` удаление реально уменьшает размер образа.

---

### `ansible.builtin.package` vs `apt`
> 📎 [ansible.builtin.package module](https://docs.ansible.com/ansible/latest/collections/ansible/builtin/package_module.html)

```yaml
- name: Install UFW package
  ansible.builtin.package:
    name: ufw
    state: present
```

Ansible имеет модули для конкретных пакетных менеджеров: `ansible.builtin.apt` (Ubuntu/Debian), `ansible.builtin.yum` (CentOS), `ansible.builtin.dnf` (Fedora).

`ansible.builtin.package` — универсальный модуль который автоматически выбирает нужный менеджер для текущей ОС. Если завтра сменить slaves с Ubuntu на CentOS — playbook не нужно менять. Для этого проекта разницы нет (slaves всегда Ubuntu), но это хорошая практика.

---

### `psycopg2-binary` vs `psycopg2`
> 📎 [psycopg2 installation](https://www.psycopg.org/docs/install.html)

```
psycopg2-binary
```

`psycopg2` — Python драйвер для PostgreSQL. При установке через pip он **компилируется из C исходников** — требует установленных `libpq-dev`, `gcc`, `python3-dev`. Без них установка падает с ошибкой.

`psycopg2-binary` — то же самое но уже скомпилированное, готовое к использованию. Не требует никаких системных зависимостей. Для разработки и Docker контейнеров — правильный выбор. Для production серверов рекомендуют обычный `psycopg2` (лучше производительность), но для этого проекта `binary` версия достаточна.

---

### `EXPOSE` в Dockerfile
> 📎 [Dockerfile EXPOSE](https://docs.docker.com/reference/dockerfile/#expose)

```dockerfile
EXPOSE 5000
```

`EXPOSE` **не открывает порт**. Это только документация — сообщает другим разработчикам и инструментам на каком порту работает приложение внутри контейнера.

Реально открывает порт только `ports:` в docker-compose:
```yaml
ports:
  - "5000:5000"   # вот это реально пробрасывает порт
```

Без `ports:` в docker-compose — `EXPOSE` ничего не делает. Можно вообще не писать `EXPOSE` и всё будет работать если `ports:` есть в docker-compose.

---

### `su - ansible` vs `su ansible`
> 📎 [su man page](https://www.man7.org/linux/man-pages/man1/su.1.html)

```bash
su - ansible   # правильно
su ansible     # неправильно для нашего случая
```

`su ansible` — переключает пользователя но оставляет текущее окружение (переменные среды, PATH, HOME). Ansible может не найти нужные файлы и коллекции.

`su - ansible` — дефис загружает полное окружение пользователя ansible: его HOME, PATH, переменные. То же самое что залогиниться под этим пользователем с нуля. Ansible найдёт всё что нужно включая установленные коллекции (`community.general`).

---

### `conn.commit()` в psycopg2
> 📎 [psycopg2 connection.commit()](https://www.psycopg.org/docs/connection.html#connection.commit)

```python
conn = get_db()
cursor = conn.cursor()
cursor.execute("INSERT INTO users ...")
conn.commit()   # без этого данные не сохранятся
```

psycopg2 по умолчанию работает в режиме **транзакций**. `cursor.execute()` выполняет SQL но не сохраняет изменения в базу — они висят в незакрытой транзакции. Без `conn.commit()` при закрытии соединения транзакция откатывается и данные пропадают.

`conn.commit()` — фиксирует транзакцию, данные записываются на диск. Для `SELECT` (чтение) commit не нужен — только для `INSERT`, `UPDATE`, `DELETE`, `CREATE TABLE`.

---

### `-it` флаги в `docker exec`
> 📎 [docker exec reference](https://docs.docker.com/reference/cli/docker/container/exec/)

```bash
docker exec -it ansible-master bash
```

`-i` (interactive) — держит STDIN открытым. Без него ты не можешь ничего вводить — команды не достигают контейнера.

`-t` (tty) — создаёт псевдотерминал. Без него нет приглашения командной строки (`$`), нет цветов, нет истории команд.

Вместе `-it` дают полноценный интерактивный терминал внутри контейнера. Если нужно просто выполнить одну команду без интерактивного режима — флаги не нужны: `docker exec ansible-master ufw status`.
