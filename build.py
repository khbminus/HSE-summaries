#!/usr/bin/env python3
# Copyright (C) Sayutin Dmitry, 2016.
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; version 3
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program; If not, see <http://www.gnu.org/licenses/>.


import sys, os, os.path, subprocess, distutils.dir_util, shutil
import hashlib, json, traceback, itertools

from concurrent.futures import ThreadPoolExecutor as Pool

if sys.version_info < (3,4):
    print("Warning: It looks like your python is old, please upgrade to at least 3.4")

try:
    import signal
    def sig_handler(ig1, ig2):
        print("Termination requested, shutting down")
        print("Just call script again to finish it's work")
        os._exit(9)
    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)
except:
    pass

COLORS_SHELL = {'red':         "\033[91;1m",
                'green':       "\033[92m",
                'yellow':      "\033[93m",
                'lightpurple': "\033[94m",
                'purple':      "\033[95m",
                'cyan':        "\033[96m",
                'lightgray':   "\033[97m",
                'black':       "\033[98m",
                'blue':        "\033[34m",
                '':            "\033[00m"} # reset

def colored_shellmode(s, color):
    return COLORS_SHELL[color] + s + COLORS_SHELL['']

def colored_modeoff(s, color):
    return s

# Global setup
colored = colored_modeoff
CALLCWD = os.path.abspath(os.getcwd())

def fix_workdir():
    global CALLCWD
    cur = CALLCWD
    while True:
        if os.path.isfile(os.path.join(cur, ".config")):
            break # found
        
        par = os.path.dirname(cur)
        if par == cur:
            # failed to discover the ordinary way (jumping upwards),
            # perform last try: check directory of script itself.
            
            scriptdir = os.path.abspath(os.path.dirname(sys.argv[0]))
            if os.path.isfile(os.path.join(scriptdir, ".config")):
                CALLCWD = '.'
                os.chdir(scriptdir)
                return

            print("Error: Failed to discover texbuild root")
            sys.exit(1)
        cur = par
    CALLCWD=os.path.relpath(CALLCWD, start=cur)
    os.chdir(cur)
fix_workdir()

class SnapshotUtil:
    def __init__(self, tmpdir, srcdir):
        self._tmpdir = tmpdir
        self._srcdir = srcdir

    def file_hash(fullpath):
        m = hashlib.sha256()
        with open(fullpath, "rb") as f:
            while True:
                obj = f.read(4096)
                if obj:
                    m.update(obj)
                else:
                    break
        return m.hexdigest()

    def get_target_hashes(self, target):
        res = []
        try:
            with open(os.path.join(self._tmpdir, '.hashes', target), "r") as f:
                for line in f:
                    spl = line.rstrip().split(sep=' ', maxsplit=1)
                    if len(spl) == 2:
                        res.append((spl[1], spl[0]))
        except FileNotFoundError:
            pass
        return res
    
    def record_target_hashes(self, target, hashes):
        outname = os.path.join(self._tmpdir, '.hashes', target)
        os.makedirs(os.path.dirname(outname), exist_ok = True)
        with open(outname, "w") as f:
            for (name, hsh) in hashes:
                f.write(hsh + " " + name + "\n")

class Bool:
    def __init__(self, s):
        if s in [True, 'True', 'true', '1', 't', 'y', 'yes', 'yeah','+']:
            self.val = True
        elif s in [False, 'False', 'false', '0', 'f', 'n', 'no', 'noo', '-']:
            self.val = False
        else:
            raise ValueError("String {}is invalid value for bool".format(s))
    def __bool__(self):
        return self.val
    def __repr__(self):
        return str(self.val)

def rmdir(path):
    if os.path.isdir(path):
        shutil.rmtree(path)

def load_conf():
    mainconf = None
    with open(".config", "r") as f:
        mainconf = json.load(f)
    try:
        with open(".localconfig", "r") as f:
            localconf = json.load(f)
            for (key, value) in localconf.items():
                mainconf[key] = value
    except FileNotFoundError:
        pass

    types = {"srcdir": str,
             "tmpdir": str,
             "outdir": str,
             "workers": int,
             "rush": Bool,
             "force": Bool,
             "color": str}
    args_left = [sys.argv[0]]
    for arg in sys.argv[1:]:
        if arg.startswith("--"):
            spl = arg.split('=', maxsplit=2)
            if len(spl) != 2:
                print("[cmd-config] Invalid option, please specify option value")
                sys.exit(1)
            mainconf[spl[0][2:]] = types[spl[0][2:]](spl[1])
        else:
            args_left.append(arg)
    sys.argv = args_left
    
    # verify conf
    def error(reason):
        print("[config] Invalid config: {}".format(reason))
        sys.exit(1)
    
    for (field, tp) in types.items():
        if not (field in mainconf and isinstance(mainconf[field], tp)):
            if field in mainconf and tp == Bool and isinstance(mainconf[field], bool):
                mainconf[field] = Bool(mainconf[field])
            else:
                error("{} is not specified or has wrong type, see manual".format(field))
    if not (mainconf["color"]  in {"none", "auto", "shell"}):
        error("color has incompatible value, see manual")
        
    for elem in mainconf['targets']:
        if not isinstance(elem, str):
            error("Expected string target, but found: {}, see manual".format(elem))
    
    return mainconf

def load_target_conf(target, gconf):
    conf = None
    with open(os.path.join(gconf['srcdir'], target, '.tgconfig'), "r") as f:
        conf = json.load(f)
    
    if not ('main' in conf and isinstance(conf['main'], str)):
        print("{}: Invalid config in {}, 'main' is not defined or has wrong type, see manual".format(colored("Error", "red"), target))
    return conf

NUM_SCS= itertools.count()

def build_target(target, config, util, hashes_new):
    try:
        tmpdir = os.path.join(config['tmpdir'], target)
        rmdir(tmpdir) # clean up old data
        os.makedirs(tmpdir)
        distutils.dir_util.copy_tree(os.path.join(config['srcdir'], target), tmpdir)
        lconf = load_target_conf(target, config)
        if os.path.exists(os.path.join(tmpdir, lconf['main'] + '.tex')):
            nonzero = False
            # we need to run xelatex twice to get table of contents setup'ed correctly.
            for i in range(1 if config['rush'] else 2):
                try:
                    proc = subprocess.Popen(['xelatex', '-interaction=nonstopmode', '-halt-on-error', lconf['main'] + '.tex'], stdout=subprocess.DEVNULL, cwd=tmpdir)
                    proc.wait()
                    if proc.returncode != 0:
                        nonzero = True
                        break
                except FileNotFoundError:
                    print("[{}] Failed to built {}, check that tex is installed".format(colored("!!!", "red"), target))
                    return
            PRINT = ""
            if not nonzero:
                PRINT = PRINT + "[{}] Built {}\n".format(colored("***", "green"), target)
            else:
                PRINT = PRINT + "[{}] Failed to built {}, log available in {}\n".format(colored("!!!", "red"), target, tmpdir)
                log = []
                try:
                    with open(os.path.join(tmpdir, lconf['main'] + ".log"), "r", errors="replace") as flog:
                        for line in flog:
                            log.append(line)
                        if len(log) > 20:
                            log = log[-20:]
                        for elem in log:
                            PRINT += colored('> ', "red") + elem.rstrip() + '\n'
                except:
                    PRINT += "We sinсerely tried to show you error log, but something went wrong:\n"
                    print(PRINT, end="")
                    raise
                print(PRINT, end="")
                return
        else:
            print("[{}] Error: unknown target format in {}".format(colored("!!!", "red"), target))
            return
        
        human = os.path.join('pdf', target + ".pdf")
        os.makedirs(os.path.dirname(human), exist_ok=True)
        shutil.copyfile(os.path.join(tmpdir, lconf['main'] + '.pdf'), human)
        PRINT = PRINT + "Result saved to {}, log in {}\n".format(colored(human, 'cyan'), os.path.join(tmpdir, lconf['main'] + '.log'))
        print(PRINT, end="")
        util.record_target_hashes(target, hashes_new)
        next(NUM_SCS)
    except Exception as ex:
        print("Python error: {}".format(ex))
        traceback.print_exc()

def main():
    if sys.argv[1:] == ["--help"]:
        print("TeX Build v1.1")
        print("Usage: {} list-of-options".format(sys.argv[0]))
        print("")
        print("Most important options:")
        print("--workers=<int>: use this number of threads")
        print("--force=<bool>:  rebuild even if there are no changes")
        print("--rush=<bool>:   twice faster, but poor quality")
        print("")
        print("See manual for details")
        sys.exit(1)
    if len(sys.argv) >= 2 and sys.argv[1] == "--clean":
        sys.argv[1:] = sys.argv[2:]
        config = load_conf()
        rmdir(config['tmpdir'])
        rmdir(config['outdir'])
        print("Cleaned {} and {}.".format(config['tmpdir'], config['outdir']))
        sys.exit(1)
    
    config = load_conf()
    util = SnapshotUtil(config['tmpdir'], config['srcdir'])
    pool = Pool(max_workers = config['workers'])

    if config['color'] == 'shell' or (config['color'] == 'auto' and os.name == 'posix'):
        global colored
        colored = colored_shellmode

    targets = []
    autoignore = 0
    if len(sys.argv) > 1:
        tgset = set(config['targets'])
        
        for arg in sys.argv[1:]:
            if not arg in tgset:
                print("[{}] Unknown target {}".format(colored('!!!', 'red'), arg))
        targets = sys.argv[1:]
    else:
        if CALLCWD == '.':
            targets = config['targets']
        else:
            for target in config['targets']:
                tgpath = os.path.relpath(os.path.join(config['srcdir'], target))
                cwpath = os.path.relpath(CALLCWD)
                common = os.path.commonprefix([tgpath, cwpath])
                if common == cwpath or common == tgpath:
                    targets.append(target)
                else:
                    autoignore += 1
    
    skipped = 0
    runned  = 0
    for target in targets:
        if not os.path.isdir(os.path.join(config['srcdir'], target)) or not os.path.isfile(os.path.join(config['srcdir'], target, '.tgconfig')):
            print('[{}] target {} was not found in source directory or has wrong format'.format(colored("!!!", 'red'), target))
            continue
        hashes_old = util.get_target_hashes(target)
        hashes_new = []
        for root, _, files in os.walk(os.path.join(config['srcdir'], target)):
            for f in files:
                path = os.path.join(root, f)
                hashes_new.append((os.path.relpath(path, config['srcdir']), SnapshotUtil.file_hash(path)))
        hashes_old.sort()
        hashes_new.sort()
        if config['force'] or hashes_old != hashes_new:
            pool.submit(build_target, target, config, util, hashes_new)
            runned += 1
        else:
            skipped += 1
    pool.shutdown()
    if skipped:
        print("Total {} {} found to be up to date.".format(colored(str(skipped), 'cyan'), "targets were" if skipped >= 2 else "target was"))
    if autoignore:
        print("Total {} {} ignored based on current directory.".format(colored(str(autoignore), 'cyan'), "targets were" if autoignore >= 2 else "target was"))
    if runned != next(NUM_SCS):
        sys.exit(2)
main()
 
