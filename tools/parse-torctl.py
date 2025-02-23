#!/usr/bin/python

import sys, os, argparse, re, json
from multiprocessing import Pool, cpu_count
from subprocess import Popen, PIPE
from signal import signal, SIGINT, SIG_IGN

DESCRIPTION="""
A utility to help parse results from tor.

This script enables processing of tor log files and storing processed
data in json format for plotting. It was written so that the log files
need never be stored on disk decompressed, which is useful when log file
sizes reach tens of gigabytes.

Use the help menu to understand usage:
$ python parse-tor.py -h

The standard way to run the script is to give the path to a directory tree
under which one or several tgen log files exist:
$ python parse-tor.py shadow.data/hosts/
$ python parse-tor.py ./

This path will be searched for log files whose names match those created
by shadow; additional patterns can be added with the '-e' option.

A single tor log file can also be passed on STDIN with the special '-' path:
$ cat tor.log | python parse-tor.py -
$ xzcat tor.log.xz | python tor-tgen.py -

The default mode is to filter and parse the log files using a single
process; this will be done with multiple worker processes when passing
the '-m' option.
"""

TORJSON="stats.tor.json"

def main():
    parser = argparse.ArgumentParser(
        description=DESCRIPTION, 
        formatter_class=argparse.RawTextHelpFormatter)#ArgumentDefaultsHelpFormatter)

    parser.add_argument(
        help="""The PATH to search for tor log files, which may be '-'
for STDIN; each log file may end in '.xz' to enable
inline xz decompression""", 
        metavar="PATH",
        action="store", dest="searchpath")

    parser.add_argument('-e', '--expression',
        help="""Append a regex PATTERN to the list of strings used with
re.search to find tgen log file names in the search path""", 
        action="append", dest="patterns",
        metavar="PATTERN",
        default=[])

    parser.add_argument('-m', '--multiproc',
        help="""Enable multiprocessing with N worker process, use '0'
to use the number of processor cores""",
        metavar="N",
        action="store", dest="nprocesses", type=type_nonnegative_integer,
        default=1)

    parser.add_argument('-p', '--prefix', 
        help="""A STRING directory path prefix where the processed data
files generated by this script will be written""", 
        metavar="STRING",
        action="store", dest="prefix",
        default=os.getcwd())

    args = parser.parse_args()
    args.searchpath = os.path.abspath(os.path.expanduser(args.searchpath))
    args.prefix = os.path.abspath(os.path.expanduser(args.prefix))
    if args.nprocesses == 0: args.nprocesses = cpu_count()
    run(args)

def run(args):
    logfilepaths = find_file_paths(args.searchpath, args.patterns)
    print >> sys.stderr, "processing input from {0} files...".format(len(logfilepaths))

    p = Pool(args.nprocesses)
    r = []
    try:
        mr = p.map_async(process_tor_log, logfilepaths)
        p.close()
        while not mr.ready(): mr.wait(1)
        r = mr.get()
    except KeyboardInterrupt:
        print >> sys.stderr, "interrupted, terminating process pool"
        p.terminate()
        p.join()
        sys.exit()

    d = {'nodes':{}}
    name_count, noname_count, success_count, error_count, total_read, total_write = 0, 0, 0, 0, 0, 0
    for item in r:
        if item is None:
            continue
        name, data = item[0], item[1]
        if name is None:
            noname_count += 1
            continue
        name_count += 1
        d['nodes'][name] = data
        boot_succeeded = item[2]
        if boot_succeeded:
            success_count += 1
        else:
            error_count += 1
            print >> sys.stderr, "warning: tor running on host '{0}' did not fully bootstrap".format(name)
        total_read += item[3]
        total_write += item[4]

    print >> sys.stderr, "done processing input: {0} boot success count, {1} boot error count, {2} files with names, {3} files without names, {4} total bytes read, {5} total bytes written".format(success_count, error_count, name_count, noname_count, total_read, total_write)
    print >> sys.stderr, "dumping stats in {0}".format(args.prefix)
    dump(d, args.prefix, TORJSON)
    print >> sys.stderr, "all done!"

def process_tor_log(filename):
    signal(SIGINT, SIG_IGN) # ignore interrupts
    source, xzproc = source_prepare(filename)

    d = {'bytes_read':{}, 'bytes_written':{}}
    name = None
    total_read, total_write = 0, 0
    boot_succeeded = False

    for line in source:
        try:
            if name is None and re.search("Starting torctl program on host", line) is not None:
                parts = line.strip().split()
                if len(parts) < 11: continue
                name = parts[10]
            elif not boot_succeeded and re.search("Bootstrapped 100", line) is not None:
                boot_succeeded = True
            elif boot_succeeded and re.search("\s650\sBW\s", line) is not None:
                parts = line.strip().split()
                if len(parts) < 11: continue
                if 'Outbound' in line: print line
                second = int(float(parts[2]))
                bwr = int(parts[9])
                bww = int(parts[10])

                if second not in d['bytes_read']: d['bytes_read'][second] = 0
                d['bytes_read'][second] += bwr
                total_read += bwr
                if second not in d['bytes_written']: d['bytes_written'][second] = 0
                d['bytes_written'][second] += bww
                total_write += bww
        except: continue # data format error

    if name is None: name = os.path.dirname(filename)

    source_cleanup(filename, source, xzproc)
    return [name, d, boot_succeeded, total_read, total_write]

def find_file_paths(searchpath, patterns):
    paths = []
    if searchpath.endswith("/-"): paths.append("-")
    else:
        for root, dirs, files in os.walk(searchpath):
            for name in files:
                found = False
                fpath = os.path.join(root, name)
                fbase = os.path.basename(fpath)
                for pattern in patterns:
                    if re.search(pattern, fbase): found = True
                if found: paths.append(fpath)
    return paths

def type_nonnegative_integer(value):
    i = int(value)
    if i < 0: raise argparse.ArgumentTypeError("%s is an invalid non-negative int value" % value)
    return i

def source_prepare(filename):
    source, xzproc = None, None
    if filename == '-':
        source = sys.stdin
    elif filename.endswith(".xz"):
        xzproc = Popen(["xz", "--decompress", "--stdout", filename], stdout=PIPE)
        source = xzproc.stdout
    else:
        source = open(filename, 'r')
    return source, xzproc

def source_cleanup(filename, source, xzproc):
    if xzproc is not None: xzproc.wait()
    elif filename != '-': source.close()

def dump(data, prefix, filename, compress=True):
    if not os.path.exists(prefix): os.makedirs(prefix)
    if compress: # inline compression
        with open("/dev/null", 'a') as nullf:
            path = "{0}/{1}.xz".format(prefix, filename)
            xzp = Popen(["xz", "--threads=3", "-"], stdin=PIPE, stdout=PIPE)
            ddp = Popen(["dd", "of={0}".format(path)], stdin=xzp.stdout, stdout=nullf, stderr=nullf)
            json.dump(data, xzp.stdin, sort_keys=True, separators=(',', ': '), indent=2)
            xzp.stdin.close()
            xzp.wait()
            ddp.wait()
    else: # no compression
        path = "{0}/{1}".format(prefix, filename)
        with open(path, 'w') as outf: json.dump(data, outf, sort_keys=True, separators=(',', ': '), indent=2)

if __name__ == '__main__': sys.exit(main())

