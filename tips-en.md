# Docker + Ansible + PostgreSQL + Python — Complete Guide

---

## What is Docker and why do we need it
> 📎 [Docker — Getting Started](https://docs.docker.com/get-started/)

Imagine you write a program on your computer. It works. You send it to a friend — it doesn't work. Why? Because they have a different Python version, a different OS, different libraries.

**Docker solves this problem.** You package the program together with everything it needs (Python, libraries, config) into one file — an **image**. From the image you run a **container** — an isolated process that works the same everywhere.

### Core concepts

- **Image** — a ready package with the program and everything it needs. Built from a Dockerfile.
- **Container** — a running image. You can run multiple containers from one image.
- **Dockerfile** — a recipe for building an image, step-by-step instructions.
- **Docker Compose** — a tool for running multiple containers together.
- **Volume** — a folder outside the container for persistent data storage.
- **Network** — a virtual network inside which containers see each other by name.

---

## How app/db fit together with Ansible master/slave

This is a common question. Here is the simple answer:

`app/docker-compose.yml` and `database/docker-compose.yml` are the same files in both cases. The difference is only **where** they are run.

**Locally (for development)** — you run them directly on your machine:
```
docker compose -f database/docker-compose.yml up -d
docker compose -f app/docker-compose.yml up -d
```

**Via Ansible (like in production)** — master connects to slave servers over SSH and runs the same files, but on them:
```
ansible-slave-1 — a separate "server" (container)
    → Ansible clones your repository onto it
    → Ansible runs: docker compose -f app/docker-compose.yml up -d
    → Now app runs ON slave-1

ansible-slave-2 — second "server"
    → Ansible clones the repository
    → Ansible runs: docker compose -f database/docker-compose.yml up -d
    → Now db runs ON slave-2
```

**Correct development order:**
1. First write app and db — develop locally, test that everything works
2. Then create Ansible — when app and db are ready, describe how to deploy them to servers

The idea: first build a working product, then automate its deployment.

---

## How the project works — full picture

```
Host (your computer)
│
├── database/docker-compose.yml
│   ├── postgres-db (PostgreSQL, port 5432)
│   └── pgadmin_gui (pgAdmin, port 5050)
│   └── network: my-network
│
├── app/docker-compose.yml
│   └── ansible-python-app (Flask, port 5000)
│   └── connects to network: my-network → finds postgres-db
│
└── ansible/docker-compose.yml
    ├── ansible-master
    │   └── runs playbook → SSH → slave-1, slave-2
    ├── ansible-slave-1 (app server)
    │   └── clones repo → docker compose up -d (app)
    └── ansible-slave-2 (db server)
        └── clones repo → docker compose up -d (database)
```

---

## Project file breakdown

---

### `ansible-master/Dockerfile`
> 📎 [Dockerfile reference](https://docs.docker.com/reference/dockerfile/)

```dockerfile
FROM ubuntu:latest
```
We use clean Ubuntu — master is the server that manages the others, we need a full OS.

```dockerfile
ENV DEBIAN_FRONTEND=noninteractive
```
Tells apt not to ask questions during installation — otherwise `docker build` will hang waiting for input in the terminal.

```dockerfile
RUN apt-get update && apt-get install -y \
    curl vim git openssh-server ansible nano sudo python3 \
    && rm -rf /var/lib/apt/lists/*
```
- `openssh-server` — needed so we can get into the container via `docker exec`
- `ansible` — the main tool, runs playbook on slave servers over SSH
- `python3` — Ansible is written in Python, requires it to work
- `git` — needed by Ansible for the `git` module in the playbook
- `rm -rf /var/lib/apt/lists/*` — removes apt cache to reduce image size

```dockerfile
RUN mkdir -p /var/run/sshd
```
SSH daemon requires this directory for its PID file — without it, it won't start. `-p` creates all intermediate directories if they don't exist.

```dockerfile
RUN useradd -m -s /bin/bash ansible && \
    echo 'ansible:123' | chpasswd && \
    echo 'ansible ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers
```
Creates the `ansible` user from which we run the playbook. Password `123` matches the password in `ansible-slave/Dockerfile` — the same user on all servers. `NOPASSWD:ALL` — user can run sudo without a password. See `ansible/inventory.ini` — it says `ansible_user=ansible`.

```dockerfile
RUN mkdir -p /home/ansible/.ssh && \
    chown ansible:ansible /home/ansible/.ssh && \
    chmod 700 /home/ansible/.ssh
```
Creates the SSH keys folder for the ansible user. `chmod 700` is required — SSH will reject connections if the directory is accessible to other users.

```dockerfile
RUN ssh-keygen -t rsa -f /home/ansible/.ssh/id_rsa -N ""
```
Generates an SSH key pair:
- `id_rsa` — private key, stays on master, used to connect to slaves
- `id_rsa.pub` — public key, copied to slaves via shared volume (see `entrypoint.sh`)
- `-N ""` — empty passphrase, otherwise SSH will ask for a password on every connection and the playbook will hang

```dockerfile
RUN chown -R ansible:ansible /home/ansible/.ssh && \
    chmod 600 /home/ansible/.ssh/id_rsa
```
`chmod 600` on the private key is required — SSH intentionally rejects the key if it is accessible to anyone other than the owner. This is protection against key leakage.

```dockerfile
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
CMD ["/entrypoint.sh"]
```
When the container starts, `entrypoint.sh` runs — it copies the public key to the shared volume and starts the SSH server.

---

### `ansible-master/entrypoint.sh`
> 📎 [sshd man page](https://www.man7.org/linux/man-pages/man8/sshd.8.html)

```bash
#!/bin/bash
```
Specifies that the script is executed via bash.

```bash
cp /home/ansible/.ssh/id_rsa.pub /shared/authorized_keys
```
Copies the master's public key to the `/shared` folder. This folder is a shared volume from `ansible/docker-compose.yml` that both master and both slaves can see. The slave reads this file at startup and puts it in its own `authorized_keys` — see `ansible-slave/entrypoint.sh`.

```bash
exec /usr/sbin/sshd -D
```
Starts the SSH server. `-D` — foreground mode, does not go to background. `exec` replaces the current process — Docker tracks exactly this process and knows when the container has finished.

---

### `ansible-slave/Dockerfile`
> 📎 [Dockerfile reference](https://docs.docker.com/reference/dockerfile/)

```dockerfile
FROM ubuntu:latest
```
We use clean Ubuntu — slave is just a server, we need a minimal OS to install everything else on.

```dockerfile
ENV DEBIAN_FRONTEND=noninteractive
```
Tells apt not to ask questions during installation — otherwise `docker build` will hang.

```dockerfile
RUN apt-get update && apt-get install -y \
    openssh-server git docker.io docker-compose \
    && apt-get -y autoremove && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
```
- `openssh-server` — needed so master can connect to slave over SSH
- `git` — needed to clone the repository from GitHub when Ansible runs the playbook
- `docker.io` — Docker Engine, needed so slave can run containers
- `docker-compose` — needed so slave can run `docker compose up -d`
- `autoremove` and `clean` — removes unnecessary dependencies and cache, reduces image size

```dockerfile
RUN mkdir -p /var/run/sshd
```
SSH daemon won't start without this directory — it writes its PID file there.

```dockerfile
RUN useradd -m -s /bin/bash ansible && \
    echo 'ansible:123' | chpasswd && \
    echo 'ansible ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers
```
Creates the `ansible` user — master will connect via SSH as this user. See `ansible/inventory.ini` — it says `ansible_user=ansible`.

```dockerfile
RUN mkdir -p /home/ansible/.ssh && \
    chown ansible:ansible /home/ansible/.ssh && \
    chmod 700 /home/ansible/.ssh
```
Creates the SSH keys folder. `chmod 700` is required — SSH will reject connections if the directory is accessible to other users.

```dockerfile
RUN usermod -aG docker ansible
```
Adds the ansible user to the docker group — without this ansible cannot run docker commands. But due to different GIDs on the host, this is not enough — see `entrypoint.sh` where we do `chmod 666`.

```dockerfile
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
CMD ["/entrypoint.sh"]
```
When the container starts, `entrypoint.sh` runs — it sets up SSH keys and starts the SSH server.

---

### `ansible-slave/entrypoint.sh`
> 📎 [Docker volumes](https://docs.docker.com/storage/volumes/) · [Docker socket](https://docs.docker.com/engine/install/linux-postinstall/)

```bash
#!/bin/bash
```
Specifies that the script is executed via bash.

```bash
chmod 666 /var/run/docker.sock
```
Docker socket is mounted from the host via `ansible/docker-compose.yml`. The `docker` group inside the container has a different GID than on the host — so even adding ansible to the docker group doesn't help. `chmod 666` opens the socket to all users.

```bash
cp /shared/authorized_keys /home/ansible/.ssh/authorized_keys
chown ansible:ansible /home/ansible/.ssh/authorized_keys
chmod 600 /home/ansible/.ssh/authorized_keys
```
Copies the master's public key from the shared volume to the correct location. SSH reads `authorized_keys` and lets in whoever's key is there — that is, master. `chmod 600` is required — SSH will reject if permissions are wider.

**Why not mount the volume directly to `authorized_keys`?** Docker creates a directory instead of a file if you mount a volume to a non-existent path — SSH expects a file and rejects it.

```bash
exec /usr/sbin/sshd -D
```
Starts the SSH server — master will connect to slave through it.

---

### `ansible/docker-compose.yml`
> 📎 [Compose file reference](https://docs.docker.com/compose/compose-file/)

```yaml
ansible-master:
  image: igorvakul/ansible-master:latest
```
We use the image from DockerHub that we built with `docker build` and pushed with `docker push`.

```yaml
    volumes:
      - shared_keys:/shared
      - ./:/ansible-project
```
- `shared_keys:/shared` — shared volume, master writes the public key here. The same volume on both slaves — they read the key from here in their `entrypoint.sh`
- `./:/ansible-project` — mounts the current `ansible/` folder into the container. This way `inventory.ini` and `deploy.yml` are visible inside master without copying into the image

```yaml
  ansible-slave-1:
    volumes:
      - shared_keys:/shared
      - /var/run/docker.sock:/var/run/docker.sock
```
- `shared_keys:/shared` — same volume as master, slave reads the public key from here in `entrypoint.sh`
- `/var/run/docker.sock:/var/run/docker.sock` — mounts Docker socket from the host. Without this slave cannot run `docker compose up -d`

```yaml
volumes:
  shared_keys:
```
Declares a named volume — Docker creates and manages it automatically. Lives until you delete it with `docker compose down -v`.

```yaml
networks:
  ansible-network:
    driver: bridge
```
All three containers are in the same network — they can reach each other by names `ansible-slave-1`, `ansible-slave-2`. Ansible connects to slaves by these names from `inventory.ini`.

---

### `ansible/inventory.ini`
> 📎 [Ansible inventory](https://docs.ansible.com/ansible/latest/inventory_guide/intro_inventory.html)

```ini
[webservers]
ansible-slave-1 ansible_user=ansible
```
- `[webservers]` — server group. In `deploy.yml` we write `hosts: webservers` — Ansible takes all servers from this group
- `ansible-slave-1` — hostname. Works because all are in the same `ansible-network` from `ansible/docker-compose.yml`
- `ansible_user=ansible` — connect as the `ansible` user. This user is created in `ansible-slave/Dockerfile`

```ini
[dbservers]
ansible-slave-2 ansible_user=ansible
```
Second group — database server. The second play in `deploy.yml` runs on exactly this group.

```ini
[all:vars]
ansible_ssh_common_args='-o StrictHostKeyChecking=no'
```
On the first SSH connection to a new server, the system asks "Do you trust this server? (yes/no)". In automated mode there is no one to answer — the playbook will hang. This flag tells it to trust all servers without asking.

---

### `ansible/deploy.yml`
> 📎 [Ansible playbooks](https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_intro.html) · [become/privilege escalation](https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_privilege_escalation.html)

```yaml
- hosts: webservers
```
This play runs only on servers from the `webservers` group in `inventory.ini` — that is, on `ansible-slave-1`.

```yaml
    - name: Clone repository
      git:
        repo: https://github.com/Igor-Vakul/lh-mini-project-docker.git
        dest: /home/ansible/project
        force: yes
```
Ansible's `git` module clones the repository to the slave server at `/home/ansible/project`. `force: yes` — overwrite if already exists. The repository is needed on slave because the `docker-compose.yml` files are there which slave will run.

```yaml
    - name: Deploy Ansible Slave-1(Application) using Docker Compose
      shell: docker compose up -d
      args:
        chdir: /home/ansible/project/app
```
The `shell` module runs the command on the slave. `chdir` — changes to folder `/home/ansible/project/app` before executing — that is where `app/docker-compose.yml` is which will start the Python application.

```yaml
- hosts: dbservers
```
Second play — runs on `ansible-slave-2` from the `dbservers` group. Same steps but `chdir` points to `/home/ansible/project/database` — starts PostgreSQL + pgAdmin.

---

### UFW — Firewall (Bonus)
> 📎 [community.general.ufw module](https://docs.ansible.com/ansible/latest/collections/community/general/ufw_module.html) · [UFW Ubuntu docs](https://help.ubuntu.com/community/UFW)

**What is UFW?**
UFW (Uncomplicated Firewall) — a firewall that controls which network connections are allowed on the server. By default on Linux everything is open — any port is accessible to anyone. UFW lets you close everything and open only what is needed.

**How UFW works under the hood:**
UFW is a wrapper around `iptables`. `iptables` is a Linux kernel tool that filters packets. So UFW requires kernel access — in Docker containers this doesn't work by default.

**Why `cap_add` is needed in docker-compose.yml:**
```yaml
cap_add:
  - NET_ADMIN
  - NET_RAW
```
Docker runs containers with restricted permissions for security. `NET_ADMIN` grants the right to manage network settings (iptables, UFW). `NET_RAW` grants the right to work with raw network packets. Without these permissions UFW returns `Permission denied`.

---

**UFW play breakdown in `deploy.yml`:**

```yaml
- name: Configure UFW
  hosts: all
  become: true
```
`become: true` at the play level — all tasks run as root via sudo. This is required for UFW — it needs root permissions. `hosts: all` — applies to both slave-1 and slave-2.

```yaml
    - name: Deny incoming connections by default
      community.general.ufw:
        direction: incoming
        policy: deny
```
Closes all incoming connections by default. After this nothing from outside can connect to the server — on any port. The next tasks will open specific needed ports.

```yaml
    - name: Allow outgoing connections by default
      community.general.ufw:
        direction: outgoing
        policy: allow
```
Outgoing connections are open — the server can reach out (for example clone a repository from GitHub).

```yaml
    - name: Allow SSH port
      community.general.ufw:
        rule: allow
        port: '22'
        proto: tcp
```
Opens port 22 — the SSH port. This must be done BEFORE enabling UFW, otherwise Ansible will lose the SSH connection and the playbook will hang.

```yaml
    - name: Enable UFW
      community.general.ufw:
        state: enabled
```
Enables UFW — from this point the rules take effect.

---

**Ports on webservers (slave-1 — application):**

```yaml
    - name: Allow port 5000
      community.general.ufw:
        rule: allow
        port: '5000'
        proto: tcp
```
Port 5000 — Flask application. Open to everyone (`src:` not specified) — users must have access to the site.

**Result on slave-1:**
- `22/tcp` — SSH (for Ansible and management)
- `5000/tcp` — Flask application (for users)

---

**Ports on dbservers (slave-2 — database):**

```yaml
    - name: Allow port 5432 only from slave-1
      community.general.ufw:
        rule: allow
        port: '5432'
        proto: tcp
        src: 172.28.0.10
```
Port 5432 — PostgreSQL. `src: 172.28.0.10` — allow only from slave-1's IP address. The database should only be accessible to the application, not to the outside world. `172.28.0.10` — static IP we assigned to slave-1 in `docker-compose.yml`.

**Why a static IP is needed for slave-1:**
Docker assigns IPs dynamically — on every restart the IP can change. So we assign a fixed IP in advance via `ipv4_address` in `docker-compose.yml`. Without a static IP we don't know what `src:` to write in the UFW rule.

**Result on slave-2:**
- `22/tcp` — SSH (for Ansible)
- `5432/tcp` from `172.28.0.10` — PostgreSQL from slave-1 only

---

**How to verify UFW is working:**
```bash
# Enter slave-1
docker exec -it ansible-slave-1 bash
ufw status

# Enter slave-2
docker exec -it ansible-slave-2 bash
ufw status
```

Expected result on slave-1:
```
Status: active
22/tcp    ALLOW  Anywhere
5000/tcp  ALLOW  Anywhere
```

Expected result on slave-2:
```
Status: active
22/tcp    ALLOW  Anywhere
5432/tcp  ALLOW  172.28.0.10
```

---

**Static IPs in `ansible/docker-compose.yml`:**
```yaml
ansible-slave-1:
  networks:
    ansible-network:
      ipv4_address: 172.28.0.10
```
Dictionary format (not a list with `-`) — required when you need to set additional network parameters. `ipv4_address` requires a `subnet` to be defined in the `networks` section.

```yaml
networks:
  ansible-network:
    driver: bridge
    ipam:
      config:
        - subnet: 172.28.0.0/16
```
`ipam` — IP Address Management. `subnet: 172.28.0.0/16` — address range for this network. `/16` means the first two octets are fixed (`172.28`), the third and fourth are free. The subnet must be unique — must not overlap with other Docker networks on the machine (check: `docker network ls`).

---

### `database/docker-compose.yml`
> 📎 [postgres — Docker Hub](https://hub.docker.com/_/postgres) · [pgAdmin — Docker Hub](https://hub.docker.com/r/dpage/pgadmin4)

```yaml
db:
  image: postgres:16
  container_name: postgres-db
```
We pin to `postgres:16` — `latest` pulls PostgreSQL 18 which changed the data directory path and the container crashes. `container_name: postgres-db` — the application finds the database by this name. See `app/docker-compose.yml` line `DB_HOST=postgres-db`.

```yaml
    environment:
      POSTGRES_USER: root
      POSTGRES_PASSWORD: 1
      POSTGRES_DB: mydb
```
These variables must match the variables in `app/docker-compose.yml` — `DB_USER=root`, `DB_PASSWORD=1`, `DB_NAME=mydb`. Otherwise the application won't be able to connect to the database.

```yaml
    volumes:
      - postgres_data:/var/lib/postgresql/data
```
PostgreSQL writes data to `/var/lib/postgresql/data`. Without a volume all data will be lost when the container stops.

```yaml
  pgadmin:
    image: dpage/pgadmin4:latest
    ports:
      - "5050:80"
```
pgAdmin — web interface for managing the database. Opens at `http://localhost:5050`. Inside the container it runs on port `80`, accessible from outside on `5050`.

```yaml
networks:
  my-network:
    name: my-network
    driver: bridge
```
`name: my-network` is required — without it Docker adds a folder prefix and the network will be called `database_my-network`. Then `app/docker-compose.yml` won't find it by the name `my-network`.

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
These variables are passed into the container and read in `app.py` via `os.environ.get('DB_HOST')`. `DB_HOST=postgres-db` — name of the database container. Docker DNS resolves it to an IP address inside the `my-network` network.

```yaml
networks:
  my-network:
    external: true
```
The network is created in `database/docker-compose.yml`. `external: true` — don't create a new one, connect to the existing one. The database must be started first, otherwise the network doesn't exist and the app won't start.

---

### `app/Dockerfile`
> 📎 [Dockerfile reference](https://docs.docker.com/reference/dockerfile/) · [Gunicorn docs](https://gunicorn.org/)

```dockerfile
FROM ubuntu:22.04
```
Ubuntu as the base — required by the task.

```dockerfile
RUN apt-get update && apt-get install -y \
    python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*
```
Install Python and pip — needed to run the Flask application and install dependencies.

```dockerfile
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
```
First copy only `requirements.txt` and install dependencies. Docker caches this layer — if `requirements.txt` hasn't changed, dependencies won't be reinstalled on the next build. This saves build time.

```dockerfile
COPY src/ .
```
Copy only the `src/` folder — it contains `app.py` and `templates/`. Other files (docker-compose.yml, Dockerfile) are not needed in the container.

```dockerfile
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "app:app"]
```
Run via gunicorn rather than `python app.py` — gunicorn is a production server that handles multiple requests concurrently. `app:app` — file `app.py`, object `app = Flask(__name__)`.

---

### `app/src/app.py`
> 📎 [Flask docs](https://flask.palletsprojects.com/) · [psycopg2 docs](https://www.psycopg.org/docs/)

```python
load_dotenv()
```
Loads variables from a `.env` file. When running via Docker this file is not used — variables come from `docker-compose.yml`. Only needed for local run via `python app.py`.

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
Connects to PostgreSQL using environment variables from `app/docker-compose.yml`. `DB_HOST=postgres-db` — the database container name that Docker DNS resolves to an IP inside the `my-network` network.

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
Creates the `users` table if it doesn't exist. Called at module level — runs on every startup including gunicorn. If removed from here and left only in `if __name__ == '__main__':` — gunicorn won't call it and the table won't be created — all requests will fail with `relation "users" does not exist`.

```python
@app.route('/users', methods=['GET'])
def get_users():
```
Endpoint called when the frontend does `fetch('/users')` in `templates/index.html`. Returns a list of all users from the table in JSON format.

```python
@app.route('/users', methods=['POST'])
def create_user():
```
Accepts JSON with `username`, `email`, `password` and adds a record to the table. Returns the created user with their `id`.

```python
@app.route('/')
def index():
    return render_template('index.html')
```
Serves the HTML page. `templates/index.html` must be in the `templates/` folder next to `app.py` — Flask looks for templates there.

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
Makes a `GET /users` request to the Flask application. Flask returns JSON with the list of users — JavaScript inserts each one as a table row.

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
`e.preventDefault()` — cancels the default form behavior (page reload). Sends `POST /users` with form data. After successful creation clears the form and reloads the user list.

```javascript
loadUsers();
```
Called immediately when the page opens — loads existing users from the database.

---

## All commands

### Build images
```bash
docker build -t igorvakul/ansible-master:latest ./ansible-master
docker build -t igorvakul/ansible-slave:latest ./ansible-slave
docker push igorvakul/ansible-master:latest
docker push igorvakul/ansible-slave:latest
```

### Database
```bash
# Start
docker compose -f database/docker-compose.yml up -d

# Stop (keep data)
docker compose -f database/docker-compose.yml down

# Stop and delete data
docker compose -f database/docker-compose.yml down -v
```

### Application
```bash
# Start (with image rebuild)
docker compose -f app/docker-compose.yml up -d --build

# Stop
docker compose -f app/docker-compose.yml down
```

### Ansible infrastructure
```bash
# Full restart (always down before up if images changed)
docker compose -f ansible/docker-compose.yml down
docker compose -f ansible/docker-compose.yml up -d
```

### Run playbook
```bash
# Enter master
docker exec -it ansible-master bash

# Switch to ansible user
su - ansible

# Go to files folder
cd /ansible-project

# Run playbook
ansible-playbook -i inventory.ini deploy.yml
```

### Check logs
```bash
docker logs ansible-python-app
docker logs postgres-db
docker logs ansible-slave-1
```

### Check running containers
```bash
docker ps
```

### Git
```bash
git add .
git commit -m "message"
git push
```

### Useful debugging commands
```bash
# Enter a container
docker exec -it <container_name> bash

# List files inside container
ls -la /home/ansible/.ssh/

# Check file contents
cat /home/ansible/.ssh/authorized_keys

# Check networks
docker network ls

# Check volumes
docker volume ls
```

---

## Starting the project from scratch

Always start in this order — each step depends on the previous one.

**1. Database** (creates the `my-network` network)
```bash
docker compose -f database/docker-compose.yml up -d
```

**2. Application** (connects to the `my-network` network created by the DB)
```bash
docker compose -f app/docker-compose.yml up -d --build
```

**3. Ansible infrastructure**
```bash
docker compose -f ansible/docker-compose.yml up -d
```

**4. Run playbook** (clones repo to slaves, runs docker compose on each, configures UFW)
```bash
docker exec -it ansible-master bash
su - ansible
cd /ansible-project
ansible-playbook -i inventory.ini deploy.yml
```

**5. Verify**
- Site: `http://localhost:5000`
- pgAdmin: `http://localhost:5050`
- UFW on slave-1: `docker exec -it ansible-slave-1 bash` → `ufw status`
- UFW on slave-2: `docker exec -it ansible-slave-2 bash` → `ufw status`

---

## Docker Hub — building and publishing images
> 📎 [Docker Hub docs](https://docs.docker.com/docker-hub/)

Docker Hub is an image registry (like GitHub but for Docker). Images are stored there so any machine can download them with `docker pull`.

**Why it's needed in the project:**
`ansible/docker-compose.yml` uses `image: igorvakul/ansible-master:latest` — Docker downloads this image from Docker Hub at startup. If the image hasn't been pushed — the container won't start.

**Workflow:**
```bash
# 1. Build image locally
docker build -t igorvakul/ansible-master:latest ./ansible-master
docker build -t igorvakul/ansible-slave:latest ./ansible-slave

# 2. Push to Docker Hub
docker push igorvakul/ansible-master:latest
docker push igorvakul/ansible-slave:latest
```

**When rebuild and push is needed:**
- `Dockerfile` changed
- `entrypoint.sh` changed
- Everything else (docker-compose.yml, deploy.yml, inventory.ini) — no rebuild needed, files are mounted via volumes or cloned from GitHub

---

## Git — branches
> 📎 [Git branching](https://git-scm.com/book/en/v2/Git-Branching-Branches-in-a-Nutshell)

**Why branches are needed:**
Branches let you keep different versions of code at the same time. For example: `main` — clean code without comments, `with-comments` — same code with explanations.

```bash
# Create new branch and switch to it
git checkout -b with-comments

# Switch to existing branch
git checkout main

# List all branches
git branch

# Push branch to GitHub
git push origin with-comments
```

**How it works:**
- Each branch is an independent copy of the code
- Changes in `with-comments` don't affect `main`
- You can switch between branches — files change automatically

---

## Common project errors

### `postgres:latest` crashes on startup
**Cause:** `latest` pulls PostgreSQL 18 which changed the data directory path.
**Fix:** Pin the version to `postgres:16` in `database/docker-compose.yml`.

---

### Network `my-network` not found
**Error:** `network my-network declared as external, but could not be found`
**Cause:** Docker adds a folder prefix to the network name — `database_my-network` instead of `my-network`.
**Fix:** Add `name: my-network` to the networks section in `database/docker-compose.yml`.

---

### `authorized_keys` created as a directory
**Cause:** Docker creates a directory instead of a file if you mount a volume directly to `authorized_keys`.
**Fix:** Mount the volume to `/shared`, copy the file in `entrypoint.sh`.

---

### Docker socket — Permission denied
**Error:** `permission denied while trying to connect to Docker daemon socket`
**Cause:** The GID of the `docker` group inside the container differs from the host — `usermod -aG docker ansible` doesn't help.
**Fix:** Add `chmod 666 /var/run/docker.sock` to `ansible-slave/entrypoint.sh`.

---

### SSH key mismatch after rebuilding master
**Cause:** `docker build` generates a new key pair — the old public key on slave no longer matches the new private key on master.
**Fix:** After rebuilding master do a full restart: `docker compose down` → `docker compose up -d` so entrypoint.sh copies the new key.

---

### `become: true` inside module parameters
**Cause:** `become: true` is indented inside the task instead of at the play level.
**Fix:** `become: true` must be at the same level as `tasks:` and `hosts:`.

```yaml
# Wrong
- community.general.ufw:
    rule: allow
    become: true     # ← inside module

# Correct
- hosts: webservers
  become: true       # ← play level
  tasks:
    - community.general.ufw:
        rule: allow
```

---

### Subnet overlap when creating a network
**Error:** `invalid pool request: Pool overlaps with other one on this address space`
**Cause:** The specified subnet is already used by another Docker network on the machine.
**Fix:** Check occupied subnets (`docker network inspect $(docker network ls -q) --format "{{.Name}}: {{range .IPAM.Config}}{{.Subnet}}{{end}}"`) and pick a free one.

---

## Non-obvious details

### `force: yes` in the git module
> 📎 [ansible.builtin.git module](https://docs.ansible.com/ansible/latest/collections/ansible/builtin/git_module.html)

```yaml
- name: Clone repository
  git:
    repo: https://github.com/...
    dest: /home/ansible/project
    force: yes
```

Without `force: yes` — if the folder `/home/ansible/project` already exists (for example after a second playbook run), Ansible will fail with an error. `force: yes` overwrites the existing repository. This allows running the playbook repeatedly without errors.

---

### `-o StrictHostKeyChecking=no` in inventory.ini
> 📎 [SSH config options](https://www.ssh.com/academy/ssh/config)

```ini
[all:vars]
ansible_ssh_common_args='-o StrictHostKeyChecking=no'
```

On the first SSH connection to a new server the system asks: "Do you trust this server? (yes/no)". In automated mode there is no one to answer — the playbook hangs and waits forever. This flag tells it to trust all servers without asking. In production this is not recommended — but for local containers it's fine.

---

### `exec` in entrypoint.sh
> 📎 [exec — Linux man page](https://www.man7.org/linux/man-pages/man3/exec.3.html)

```bash
exec /usr/sbin/sshd -D
```

If you write just `/usr/sbin/sshd -D` without `exec` — bash starts sshd as a child process. Docker tracks PID 1 (the first process) — that would be bash. When bash finishes, Docker stops the container, even if sshd is still running.

`exec` replaces the current bash process with sshd — sshd becomes PID 1. Docker now tracks exactly it. The container lives as long as sshd lives.

---

### `--no-cache-dir` in pip
> 📎 [pip install docs](https://pip.pypa.io/en/stable/cli/pip_install/#cmdoption-no-cache-dir)

```dockerfile
RUN pip3 install --no-cache-dir -r requirements.txt
```

pip by default caches downloaded packages in `/root/.cache/pip`. In Docker this cache is useless — the next build will run pip again anyway (new layer). `--no-cache-dir` doesn't save the cache — the image is smaller.

---

### `depends_on` in docker-compose
> 📎 [Compose depends_on](https://docs.docker.com/compose/compose-file/05-services/#depends_on)

```yaml
pgadmin:
  depends_on:
    - db
```

`depends_on` only guarantees the **startup order** of containers — first `db`, then `pgadmin`. But it does NOT guarantee that PostgreSQL is **ready to accept connections** when pgadmin starts. PostgreSQL takes a few seconds to start. If pgadmin connects too quickly — it will get an error. In production you use `healthcheck`, for this project `depends_on` is sufficient.

---

### `restart: always`
> 📎 [Compose restart policy](https://docs.docker.com/compose/compose-file/05-services/#restart)

```yaml
restart: always
```

The container will automatically restart if:
- It crashed with an error
- Docker daemon restarted (for example after a computer reboot)

Without `restart: always` — after a computer reboot all containers are stopped and need to be started manually.

---

### `-D` flag in sshd
> 📎 [sshd man page](https://www.man7.org/linux/man-pages/man8/sshd.8.html)

```bash
/usr/sbin/sshd -D
```

By default sshd starts in background mode (daemon) — goes to background and returns control to the terminal. For Docker this is a problem: if entrypoint.sh finishes, Docker considers the container done and stops it.

`-D` — foreground mode, sshd doesn't go to background. entrypoint.sh "hangs" on this line and keeps the container alive as long as sshd is running.

---

### Named volume vs bind mount
> 📎 [Docker storage overview](https://docs.docker.com/storage/)

In docker-compose two different types of mounting are used:

```yaml
volumes:
  - shared_keys:/shared        # named volume
  - ./:/ansible-project        # bind mount
  - /var/run/docker.sock:/var/run/docker.sock  # bind mount
```

**Named volume** (`shared_keys:/shared`) — Docker creates and manages the folder itself, it lives in the Docker system (`/var/lib/docker/volumes/`). Deleted only via `docker compose down -v`. Used when data needs to persist between restarts or be shared between containers.

**Bind mount** (`./:/ansible-project`) — mounts a specific folder from the host. `./` — current folder on the host, `/ansible-project` — where it appears inside the container. Changes on the host are immediately visible inside the container. Used when you need to work with host files (code, configs).

---

### Port mapping `host:container`
> 📎 [Compose ports](https://docs.docker.com/compose/compose-file/05-services/#ports)

```yaml
ports:
  - "5000:5000"   # Flask
  - "5050:80"     # pgAdmin
  - "5432:5432"   # PostgreSQL
```

Format: `host_port:container_port`

- `5000:5000` — Flask inside the container listens on port 5000, also accessible from outside on 5000. `http://localhost:5000`
- `5050:80` — pgAdmin inside the container runs on port 80 (standard HTTP), but accessible from outside on 5050. This is done to avoid occupying port 80 on the host. `http://localhost:5050`

The host port can be any free port — it doesn't have to match the container port.

---

### Ansible collections and `community.general`
> 📎 [Ansible collections guide](https://docs.ansible.com/ansible/latest/collections_guide/index.html) · [ansible-galaxy](https://docs.ansible.com/ansible/latest/galaxy/user_guide.html)

Ansible comes with a basic set of modules (`ansible.builtin`). Additional modules are distributed as **collections** — packages that need to be installed separately.

`community.general` — a community collection, contains the `ufw` module (and hundreds of others). Without it Ansible doesn't know what `community.general.ufw` is and will return an error.

```dockerfile
RUN ansible-galaxy collection install community.general
```

`ansible-galaxy` — package manager for Ansible (like pip for Python). Installed in Dockerfile so the collection is in the image — otherwise it would need to be installed every time the container starts.

---

### `chdir` in the shell module
> 📎 [ansible.builtin.shell module](https://docs.ansible.com/ansible/latest/collections/ansible/builtin/shell_module.html)

```yaml
- name: Deploy using Docker Compose
  shell: docker compose up -d
  args:
    chdir: /home/ansible/project/app
```

Why not write `cd /home/ansible/project/app && docker compose up -d`?

Technically you can, but `chdir` is the proper way in Ansible. The `shell` module launches a new shell process each time — the current directory variable is not preserved between tasks. `chdir` reliably changes to the folder before running the command and it is clearly visible in the code.

---

### Python interpreter warning in playbook output
> 📎 [Ansible interpreter discovery](https://docs.ansible.com/ansible/latest/reference_appendices/interpreter_discovery.html)

```
[WARNING]: Host 'ansible-slave-1' is using the discovered Python interpreter
at '/usr/bin/python3.14'
```

Ansible runs Python modules on slave servers. It automatically finds Python — found `/usr/bin/python3.14`. The warning says: "I found Python on my own, but if you install another version — I might find the wrong one".

This is not an error — just information. To remove the warning you can explicitly specify in `inventory.ini`:
```ini
[all:vars]
ansible_python_interpreter=/usr/bin/python3
```
For this project the warning doesn't affect functionality.

---

### `0.0.0.0` in gunicorn
> 📎 [Gunicorn configuration](https://docs.gunicorn.org/en/stable/settings.html#bind)

```dockerfile
CMD ["gunicorn", "--bind", "0.0.0.0:5000", ...]
```

`0.0.0.0` means "listen on all network interfaces". If you write `127.0.0.1:5000` (localhost) — the application will only be accessible inside the container itself. Docker won't be able to forward the port to the outside, and `http://localhost:5000` on the host won't open.

`0.0.0.0` is required for any server inside a Docker container that needs to be accessible from outside.

---

### Docker image layers
> 📎 [Docker build cache](https://docs.docker.com/build/cache/)

Every `RUN`, `COPY`, `ADD` instruction in a Dockerfile creates a new **layer**. Docker caches layers — if an instruction hasn't changed, the cache is used on the next build.

**Why commands are chained with `&&`:**
```dockerfile
# Bad — 3 layers, apt cache remains in the image
RUN apt-get update
RUN apt-get install -y git
RUN rm -rf /var/lib/apt/lists/*

# Good — 1 layer, cache is removed in the same layer
RUN apt-get update && apt-get install -y git \
    && rm -rf /var/lib/apt/lists/*
```

If `rm -rf /var/lib/apt/lists/*` is in a separate `RUN` — the apt cache is already committed in the previous layer and still takes up space in the final image. Only in one `RUN` does the deletion actually reduce the image size.

---

### `ansible.builtin.package` vs `apt`
> 📎 [ansible.builtin.package module](https://docs.ansible.com/ansible/latest/collections/ansible/builtin/package_module.html)

```yaml
- name: Install UFW package
  ansible.builtin.package:
    name: ufw
    state: present
```

Ansible has modules for specific package managers: `ansible.builtin.apt` (Ubuntu/Debian), `ansible.builtin.yum` (CentOS), `ansible.builtin.dnf` (Fedora).

`ansible.builtin.package` — a universal module that automatically selects the right manager for the current OS. If slaves are switched from Ubuntu to CentOS tomorrow — the playbook doesn't need to change. For this project there's no difference (slaves are always Ubuntu), but it's good practice.

---

### `psycopg2-binary` vs `psycopg2`
> 📎 [psycopg2 installation](https://www.psycopg.org/docs/install.html)

```
psycopg2-binary
```

`psycopg2` — Python driver for PostgreSQL. When installed via pip it **compiles from C source code** — requires `libpq-dev`, `gcc`, `python3-dev` to be installed. Without them installation fails with an error.

`psycopg2-binary` — the same thing but pre-compiled, ready to use. Requires no system dependencies. For development and Docker containers — the right choice. For production servers regular `psycopg2` is recommended (better performance), but for this project the `binary` version is sufficient.

---

### `EXPOSE` in Dockerfile
> 📎 [Dockerfile EXPOSE](https://docs.docker.com/reference/dockerfile/#expose)

```dockerfile
EXPOSE 5000
```

`EXPOSE` **does not open the port**. It is only documentation — tells other developers and tools which port the application runs on inside the container.

The port is actually opened only by `ports:` in docker-compose:
```yaml
ports:
  - "5000:5000"   # this actually forwards the port
```

Without `ports:` in docker-compose — `EXPOSE` does nothing. You can skip `EXPOSE` entirely and everything will work if `ports:` is in docker-compose.

---

### `su - ansible` vs `su ansible`
> 📎 [su man page](https://www.man7.org/linux/man-pages/man1/su.1.html)

```bash
su - ansible   # correct
su ansible     # wrong for our case
```

`su ansible` — switches the user but keeps the current environment (environment variables, PATH, HOME). Ansible may not find the needed files and collections.

`su - ansible` — the dash loads the full ansible user environment: their HOME, PATH, variables. Same as logging in as that user from scratch. Ansible will find everything it needs including installed collections (`community.general`).

---

### `conn.commit()` in psycopg2
> 📎 [psycopg2 connection.commit()](https://www.psycopg.org/docs/connection.html#connection.commit)

```python
conn = get_db()
cursor = conn.cursor()
cursor.execute("INSERT INTO users ...")
conn.commit()   # without this data won't be saved
```

psycopg2 by default works in **transaction** mode. `cursor.execute()` runs the SQL but doesn't save changes to the database — they hang in an open transaction. Without `conn.commit()` when the connection closes the transaction is rolled back and the data is lost.

`conn.commit()` — commits the transaction, data is written to disk. For `SELECT` (read) commit is not needed — only for `INSERT`, `UPDATE`, `DELETE`, `CREATE TABLE`.

---

### `-it` flags in `docker exec`
> 📎 [docker exec reference](https://docs.docker.com/reference/cli/docker/container/exec/)

```bash
docker exec -it ansible-master bash
```

`-i` (interactive) — keeps STDIN open. Without it you can't type anything — commands don't reach the container.

`-t` (tty) — creates a pseudo-terminal. Without it there's no command prompt (`$`), no colors, no command history.

Together `-it` give a full interactive terminal inside the container. If you just need to run one command without interactive mode — the flags are not needed: `docker exec ansible-master ufw status`.
