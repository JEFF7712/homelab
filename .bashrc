# ~/.bashrc

# Load Bash Completion
if [ -f /etc/profile.d/bash_completion.sh ]; then
    . /etc/profile.d/bash_completion.sh
fi

# Set a colorful prompt
export PS1='\[\033[01;32m\]\u@\h\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\$ '

# Standard Aliases
alias ll='ls -l'
alias la='ls -la'
# Colorize ls output
alias ls='ls --color=auto'

# History Settings
export HISTCONTROL=ignoredups:erasedups  # No duplicate entries
export HISTSIZE=1000                     # Big history
export HISTFILESIZE=2000                 # Big file size

# Terminal Fix For SSH
export TERM=xterm-256color

# Custom Aliases
alias temp='bash /usr/local/bin/report-temp.sh'
alias deploy='bash /usr/local/bin/deploy.sh'
