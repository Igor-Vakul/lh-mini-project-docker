#!/bin/bash
cp /home/ansible/.ssh/id_rsa.pub /shared/authorized_keys
exec /usr/sbin/sshd -D