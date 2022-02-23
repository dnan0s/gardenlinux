#!/bin/bash

set -eufo pipefail

arch="$(uname -m)"
jwt_path="$(realpath "$(dirname "$BASH_SOURCE")/jwt.sh")"
build_prefix=".build/github_action_runner-$([ "$arch" = aarch64 ] && echo arm64 || echo amd64)-dev-local"
repo="gardenlinux/gardenlinux"
api_token=
app_id=
app_rsa_key=
num_runners=1

while [ "$#" -gt 0 ]; do
	flag="$1"; shift
	case "$flag" in
		-p|--build-prefix) build_prefix="$1"; shift ;;
		-r|--repo) repo="$1"; shift ;;
		-t|--token) api_token="$1"; shift ;;
		-a|--app) app_id="$1"; shift ;;
		-k|--key) app_rsa_key="$1"; shift ;;
		-n|--num-runners) num_runners="$1"; shift ;;
	esac
done

build_prefix="$(realpath "$build_prefix")"

IFS='/' read -r repo_org repo_name <<< "$repo"

if [ -z "$repo_org" ] || [ -z "$repo_name" ]; then
	echo "invalid repo format, should be full name with owner" >&2
	exit 1
fi

if [ -n "$api_token" ]; then
	function get_token {
		echo "$api_token"
	}
elif [ -n "$app_id" ]; then
	if [ -z "$app_rsa_key" ]; then
		echo "required argument key missing" >&2
		exit 1
	fi
	if [ ! -f "$app_rsa_key" ]; then
		echo "$app_rsa_key not a file" >&2
		exit 1
	fi

	app_rsa_key="$(realpath "$app_rsa_key")"

	function get_token {
		local jwt="$("$jwt_path" "$app_id" < "$app_rsa_key")"

		local installation_id="$(curl -s -f -H "Authorization: Bearer $jwt" "https://api.github.com/app/installations" | jq -r '.[] | select(.account.login == "'"$repo_org"'") | .id')"
		local api_token="$(curl -s -f -X POST -H "Authorization: Bearer $jwt" "https://api.github.com/app/installations/$installation_id/access_tokens" | jq -r '.token')"

		echo "$api_token"
	}
else
	echo "required argument token or app id missing" >&2
	exit 1
fi

for i in "qemu-system-$arch" curl jq docker; do
	if ! command -v "$i" &> /dev/null; then
		echo "$i could not be found, but is required" >&2
		exit 1
	fi
done

if ! docker container inspect registry | jq -e '.[0].NetworkSettings.Ports["5000/tcp"]' > /dev/null 2>&1; then
	echo "local docker registry pull through cache not running or not using port 5000" >&2
	exit 1
fi

trap 'num_runners=0' USR1 # allows to gracefully wind down scheduler
trap 'jobs -r -p | xargs -r kill; num_runners=0' TERM

echo "init done, starting runners..."

while [ "$num_runners" -gt 0 ]; do
	while [ "$(jobs -r -p | wc -l)" -lt "$num_runners" ]; do
		(
			function cleanup {
				[ -z "$qemu_pid" ] || kill "$qemu_pid" || true
				[ -z "$runner_id" ] || curl -s -f -u "token:$(get_token)" -X DELETE "https://api.github.com/repos/$repo/actions/runners/$runner_id" || true
				[ -z "$dir" ] || rm -rf "$dir"
			}

			dir=
			qemu_pid=
			runner_id=

			trap 'cleanup' EXIT
			trap 'exit' TERM

			dir="$(mktemp -d /tmp/github_action_runner.XXXXXXXX)"
			cd "$dir"

			mkfifo virtio-serial-0
			mkfifo virtio-serial-1

			# start github action runner vm
			"qemu-system-$arch" \
				-accel kvm \
				$(if [ "$arch" = "aarch64" ]; then echo -machine virt; fi) \
				-cpu host \
				-smp 4 \
				-m 16G \
				-nodefaults \
				-no-reboot \
				-display none \
				-serial stdio \
				-nic user,model=virtio \
				-kernel "$build_prefix.vmlinuz" \
				-initrd "$build_prefix.initrd" \
				-append "panic=-1 lsm=lockdown console=$([ "$arch" = "aarch64" ] && echo ttyAMA0 || echo ttyS0) systemd.journald.forward_to_console=1 systemd.mask=getty.target" \
				-device virtio-serial \
				-chardev pipe,path=virtio-serial-0,id=pipe0 \
				-device virtserialport,chardev=pipe0,name=github_action_runner_in \
				-chardev pipe,path=virtio-serial-1,id=pipe1 \
				-device virtserialport,chardev=pipe1,name=github_action_runner_out \
				&> console &
			qemu_pid="$!"

			echo "[runner $BASHPID] vm $qemu_pid started"

			# generate registration token and pass to runner (pass empty token on failure to ensure vm terminates as well)
			registration_token="$(curl -s -f -u "token:$(get_token)" -X POST "https://api.github.com/repos/$repo/actions/runners/registration-token" | jq -r .token || true)"
			printf "url=https://github.com/$repo\ntoken=$registration_token\n\n" > virtio-serial-0
			read -r runner_id < virtio-serial-1

			echo "[runner $BASHPID] registered as $runner_id"

			# wait for vm to halt, continue waiting if interrupted by any signal (wait exit code > 128)
			while true; do wait && break || [ "$?" -gt 128 ]; done

			echo "[runner $BASHPID] vm $qemu_pid terminated"
			qemu_pid=
		) &
		echo "runner $! started"
	done

	# wait for any job to terminate or to be interrupted by any signal, ignore errors
	wait -p runner_pid -n || true
	[ ! -v runner_pid ] || echo "runner $runner_pid terminated"
done

echo "waiting for remaining runners to terminate..."

# once SIGUSR1 recieved wait for all remaining runners to terminate
while true; do wait && break || [ "$?" -gt 128 ]; done
