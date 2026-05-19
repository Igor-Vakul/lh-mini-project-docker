#!/bin/bash
chmod 666 /var/run/docker.sock

cp /shared/authorized_keys /home/ansible/.ssh/authorized_keys
chown ansible:ansible /home/ansible/.ssh/authorized_keys
chmod 600 /home/ansible/.ssh/authorized_keys
exec /usr/sbin/sshd -D