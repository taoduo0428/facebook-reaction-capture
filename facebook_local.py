"""Portable local launcher. No device changes are made by init/doctor."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent


def configuration(root: Path = ROOT) -> dict:
    path = root / 'facebook.local.json'
    settings = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    if not isinstance(settings, dict):
        raise ValueError('facebook.local.json must be a JSON object')
    port = settings.get('port', 8765)
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise ValueError('port must be an integer from 1024 to 65535')
    sdk = settings.get('sdk_root') or os.environ.get('ANDROID_SDK_ROOT') or os.environ.get('ANDROID_HOME')
    if not sdk:
        candidates = [Path('D:/Android/SdkManual'), Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Android/Sdk']
        sdk = next((str(p) for p in candidates if (p / 'emulator/emulator.exe').is_file()), '')
    avd = settings.get('avd_home') or os.environ.get('ANDROID_AVD_HOME')
    if not avd:
        legacy = Path('D:/Android/AVDManual')
        avd = str(legacy if sdk and Path(sdk) == Path('D:/Android/SdkManual') and legacy.is_dir() else Path.home() / '.android/avd')
    return {**settings, 'port': port, 'sdk_root': str(sdk or ''), 'avd_home': str(avd)}


def initialize(root: Path = ROOT) -> None:
    # Exclusive creation prevents overwriting another user's config or account registry.
    for target, template in [('facebook.local.json', 'facebook.local.example.json'), ('facebook_workers.json', 'facebook_workers.example.json')]:
        try:
            with (root / target).open('x', encoding='utf-8') as stream:
                stream.write((root / template).read_text(encoding='utf-8'))
        except FileExistsError:
            pass


def environment(settings: dict, root: Path = ROOT) -> None:
    os.environ['FACEBOOK_PYTHON_EXE'] = sys.executable
    paths = [str(Path(sys.executable).parent), str(Path(sys.executable).parent / 'Scripts')]
    if settings['sdk_root']:
        sdk = Path(settings['sdk_root']).expanduser().resolve()
        os.environ['ANDROID_SDK_ROOT'] = os.environ['ANDROID_HOME'] = str(sdk)
        os.environ['FACEBOOK_SDK_ROOT'] = str(sdk)
        os.environ['FACEBOOK_ADB_EXE'] = str(sdk / 'platform-tools/adb.exe')
        paths += [str(sdk / 'platform-tools'), str(sdk / 'emulator')]
    else:
        paths.append(str(root / 'platform-tools'))
    os.environ['FACEBOOK_AVD_HOME'] = os.environ['ANDROID_AVD_HOME'] = str(Path(settings['avd_home']).expanduser().resolve())
    os.environ['PATH'] = os.pathsep.join(paths + [os.environ.get('PATH', '')])


def doctor(root: Path = ROOT) -> dict:
    settings = configuration(root)
    environment(settings, root)
    checks = []
    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({'name': name, 'ok': ok, 'detail': detail})
    check('python', sys.version_info >= (3, 12), sys.version.split()[0])
    for package in ('mitmproxy', 'frida', 'frida-tools'):
        try:
            version = importlib.metadata.version(package)
            check(package, True, version)
        except importlib.metadata.PackageNotFoundError:
            check(package, False, 'Run START.cmd to install dependencies')
    check('adb', bool(shutil.which('adb')), 'Android SDK platform-tools; set sdk_root in facebook.local.json')
    check('emulator', bool(shutil.which('emulator')), 'Android SDK emulator; set sdk_root in facebook.local.json')
    registry = root / 'facebook_workers.json'
    if registry.is_file():
        from facebook_capture_api import load_worker_config
        workers = load_worker_config(registry)
        for worker in workers:
            if worker.enabled:
                check(worker.worker_id + ':avd', (Path(settings['avd_home']) / (worker.avd_name + '.ini')).is_file(), worker.avd_name)
                with socket.socket() as probe:
                    try:
                        probe.bind(('127.0.0.1', worker.capture_port))
                        available = True
                    except OSError:
                        available = False
                check(worker.worker_id + ':capture-port', available, str(worker.capture_port) + ' (occupied may mean an active capture)')
    else:
        check('worker-registry', False, 'Run START.cmd to initialize config')
    try:
        with socket.create_connection(('127.0.0.1', 7897), timeout=1):
            proxy = True
    except OSError:
        proxy = False
    check('upstream-proxy', proxy, 'HTTP proxy on 127.0.0.1:7897 required by current Android transport')
    return {'checks': checks, 'host_ready': all(x['ok'] for x in checks), 'device_acceptance': 'Manual Android setup, login and target probe required; see README.md'}


def start(no_browser: bool = False) -> None:
    if sys.platform != 'win32':
        raise ValueError('Windows capture runtime required. See macos/README.md for the limited Mac adapter.')
    settings = configuration()
    environment(settings)
    url = f"http://127.0.0.1:{settings['port']}"
    try:
        with urlopen(url + '/health', timeout=2) as response:
            existing = json.load(response)
        if existing.get('service') != 'ready' or 'workers' not in existing:
            raise ValueError('Configured port belongs to another service; change port in facebook.local.json')
    except OSError:
        existing = None
    if existing is not None:
        print('Already running: ' + url)
        if not no_browser and settings.get('open_browser', True):
            webbrowser.open(url)
        return
    from facebook_capture_api import ApiHandler, JobManager, ThreadingHTTPServer
    # Bind before constructing the manager (which loads persistent state).
    server = ThreadingHTTPServer(('127.0.0.1', settings['port']), ApiHandler)
    server.manager = JobManager()
    print('Local console: ' + url + '\nKeep this window open. Ctrl+C stops the service.')
    if not no_browser and settings.get('open_browser', True):
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['init', 'doctor', 'start', 'shell'])
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    try:
        if args.action == 'init':
            initialize()
            print('Local config ready; existing files preserved.')
        elif args.action == 'doctor':
            report = doctor()
            print(json.dumps(report, ensure_ascii=True, indent=2))
            return 0 if report['host_ready'] else 2
        elif args.action == 'shell':
            environment(configuration())
            return subprocess.call(['powershell.exe', '-NoProfile', '-NoExit', '-ExecutionPolicy', 'Bypass'], cwd=ROOT)
        else:
            start(args.no_browser)
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
