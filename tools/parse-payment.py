#!/usr/bin/python

import sys, os, argparse, re, json, datetime, time
from multiprocessing import Pool, cpu_count
from subprocess import Popen, PIPE
from signal import signal, SIGINT, SIG_IGN
#from Canvas import Line

DESCRIPTION="""
A utility to help parse payment results for moneTor output logs.

This script enables processing of payment log files and storing processed
data in json format for plotting. It was written so that the log files
need never be stored on disk decompressed, which is useful when log file
sizes reach tens of gigabytes.

Use the help menu to understand usage:
$ python parse-payment.py -h

The standard way to run the script is to give the path to a directory tree
under which one or several tor log files exist:
$ python parse-payment.py shadow.data/hosts/
$ python parse-payment.py ./

This path will be searched for log files whose names match those created
by shadow; additional patterns can be added with the '-e' option.

A single payment log file can also be passed on STDIN with the special '-' path:
$ cat payment.log | python parse-payment.py -
$ xzcat payment.log.xz | python parse-payment.py -

The default mode is to filter and parse the log files using a single
process; this will be done with multiple worker processes when passing
the '-m' option.

 """

PAYMENTJSON="stats.payment.json"

def main():
    parser = argparse.ArgumentParser(
        description=DESCRIPTION,
        formatter_class=argparse.RawTextHelpFormatter)#ArgumentDefaultsHelpFormatter)

    parser.add_argument(
        help="""The PATH to search for payment log files, which may be '-'
for STDIN; each log file may end in '.xz' to enable
inline xz decompression""",
        metavar="PATH",
        action="store", dest="searchpath")

    parser.add_argument('-e', '--expression',
        help="""Append a regex PATTERN to the list of strings used with
re.search to find payment log file names in the search path""",
        action="append", dest="patterns",
        metavar="PATTERN",
        default=["\.tor\..*\.log"])

    parser.add_argument('-f', '--filter',
        help="""Specify comma delimited list of substrings that must be found
in the filename in order to be considered""",
        action="store", dest="filters_string",
        metavar="FILTER",
        default="")

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
    logfilepaths = find_file_paths(args.searchpath, args.patterns, args.filters_string.split('|'))
    print >> sys.stderr, "processing input from {0} files...".format(len(logfilepaths))

    p = Pool(args.nprocesses)
    r = []
    try:
        mr = p.map_async(process_payment_log, logfilepaths)
        p.close()
        while not mr.ready(): mr.wait(1)
        r = mr.get()
    except KeyboardInterrupt:
        print >> sys.stderr, "interrupted, terminating process pool"
        p.terminate()
        p.join()
        sys.exit()

    d = {'nodes':{}}
    name_count, noname_count = 0, 0
    for item in r:
        if item is None:
            continue
        name, data = item[0], item[1]
        if name is None:
            noname_count += 1
            continue
        name_count += 1
        d['nodes'][name] = data

    print >> sys.stderr, "done processing input: {0} files with names, {1} files without names".format(name_count, noname_count)
    print >> sys.stderr, "dumping stats in {0}".format(args.prefix)
    dump(d, args.prefix, PAYMENTJSON)
    print >> sys.stderr, "all done!"

def process_payment_log(filename):
    signal(SIGINT, SIG_IGN) # ignore interrupts
    source, xzproc = source_prepare(filename)

    name = filename.split('/')[-1].split('-')[1].split('.')[0]

    d = {'guard': {}, 'middle': {}, 'exit': {}}
    d['guard'] = {'numpayments': {}, 'lifetime': {}, 'ttestablish': {}, 'ttpayment': {},
                  'ttpaysuccess': {}, 'ttclose': {}}
    d['middle'] = {'numpayments': {}, 'lifetime': {}, 'ttestablish': {}, 'ttpayment': {},
                  'ttpaysuccess': {}, 'ttclose': {}}
    d['exit'] = {'numpayments': {}, 'lifetime': {}, 'ttestablish': {}, 'ttpayment': {},
                  'ttpaysuccess': {}, 'ttclose': {}}

    for line in source:
        if re.search('mt_log_nanochannel', line) is not None:
            try:
                data = line.split('{')[1].split('}')[0]
                parsed = {x.split(': ')[0] : x.split(': ')[1] for x in data.split(', ')}
                second = int(parsed['time'])
                chntype = parsed['type']

                if second not in d[chntype]['numpayments']:
                    d[chntype]['numpayments'][second] = []
                if second not in d[chntype]['lifetime']:
                    d[chntype]['lifetime'][second] = []
                if second not in d[chntype]['ttestablish']:
                    d[chntype]['ttestablish'][second] = []
                if second not in d[chntype]['ttpayment']:
                    d[chntype]['ttpayment'][second] = []
                if second not in d[chntype]['ttpaysuccess']:
                    d[chntype]['ttpaysuccess'][second] = []
                if second not in d[chntype]['ttclose']:
                    d[chntype]['ttclose'][second] = []

                d[chntype]['numpayments'][second].append(int(parsed['numpayments']))
                d[chntype]['lifetime'][second].append(float(parsed['lifetime']))
                d[chntype]['ttestablish'][second].append(float(parsed['ttestablish']))
                d[chntype]['ttpayment'][second].append(float(parsed['ttpayment']))
                d[chntype]['ttpaysuccess'][second].append(float(parsed['ttpaysuccess']))
                d[chntype]['ttclose'][second].append(float(parsed['ttclose']))

            except: continue # data format error

    source_cleanup(filename, source, xzproc)
    return [name, d]

def find_file_paths(searchpath, patterns, filters):
    paths = []
    if searchpath.endswith("/-"): paths.append("-")
    else:
        for root, dirs, files in os.walk(searchpath):
            for name in files:
                found = False
                fpath = os.path.join(root, name)
                fbase = os.path.basename(fpath)
                for pattern in patterns:
                    if re.search(pattern, fbase) and not any(s not in fbase for s in filters):
                        found = True
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
