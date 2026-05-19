FROM ubuntu:latest

# Avoid interactive prompts during package installation
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

# Configure SSH
RUN mkdir -p /var/run/sshd
# Create a user for Ansible (optional but recommended)
RUN useradd -m -s /bin/bash ansible && \
    echo 'ansible:123' | chpasswd && \
    echo 'ansible ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers

# Set up SSH directory for the user
RUN mkdir -p /home/ansible/.ssh && \
    chown ansible:ansible /home/ansible/.ssh && \
    chmod 700 /home/ansible/.ssh

RUN ssh-keygen -t rsa -f /home/ansible/.ssh/id_rsa -N ""

RUN chown -R ansible:ansible /home/ansible/.ssh && chmod 600 /home/ansible/.ssh/id_rsa

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Expose SSH port
EXPOSE 22

WORKDIR /app

# Start SSH service
CMD ["/entrypoint.sh"]