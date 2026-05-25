FROM ubuntu:latest

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    curl \
    vim \
    git \
    openssh-server \
    ansible \
    nano \
    sudo \
    python3 \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /var/run/sshd

RUN useradd -m -s /bin/bash ansible && \
    echo 'ansible:123' | chpasswd && \
    echo 'ansible ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers

RUN mkdir -p /home/ansible/.ssh && \
    chown ansible:ansible /home/ansible/.ssh && \
    chmod 700 /home/ansible/.ssh

RUN ssh-keygen -t rsa -f /home/ansible/.ssh/id_rsa -N ""

RUN chown -R ansible:ansible /home/ansible/.ssh && chmod 600 /home/ansible/.ssh/id_rsa

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 22

RUN ansible-galaxy collection install community.general

WORKDIR /app

CMD ["/entrypoint.sh"]
