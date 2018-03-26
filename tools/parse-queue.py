# Tool to parse a debug logged ideal file in order to observe the
# queue length over time of an individual circuit

import sys, argparse, os, os.path
import cPickle, gzip
import matplotlib; matplotlib.use('Agg') # for systems without X11
import pylab
from itertools import cycle
from matplotlib.backends.backend_pdf import PdfPages

def save_zipped_pickle(obj, filename, protocol=-1):
    with gzip.open(filename, 'wb') as f:
        cPickle.dump(obj, f, protocol)

def load_zipped_pickle(filename):
    with gzip.open(filename, 'rb') as f:
        loaded_object = cPickle.load(f)
        return loaded_object

UNIXSTARTTIME = 946684800
CELLTHRESHOLD = 500
cache_prefix = "objcache/"

def main():
    parser = argparse.ArgumentParser(description='Parse a relay to display queue activity over time. Can handle one relay at time. The Tor branch should be monetor_ideal_0.3.2 with debug logging set to on. This program parses through all of the queue activity for each circuit, locates the circuits that are larger than some minimum number of cells (and therefore probably related to actual internet traffic rather than background Tor overhead, and displays a graph with of the comprehensive cell queue size over time of each circuit that appears in the optional start/end windows. Finally, the program automatically manages some cache files to make reviewing the same graphs faster. In other words, the first time plotting a specific relay will be slow, but all subsequent plots will be much faster', formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    parser.add_argument('-s', '--start', action="store", type=float, dest="start", help="Start time (in seconds) for graph display", metavar="START", default=0)

    parser.add_argument('-e', '--end', action="store", type=float, dest="end", help="End time (in seconds) for graph display", metavar="END", default=sys.maxsize)

    parser.add_argument('-p', '--premium', action="store_true", dest="premium", help="Plot premium circuits only", default=0)

    parser.add_argument('-n', '--nonprem', action="store_true", dest="nonprem", help="Plot nonprem circuits only", default=0)

    parser.add_argument('-t', '--threshold', action="store", type=int, dest="cellthreshold", help="Minimum number of cells before we consider plotting a circuit. Should probably leave at default", metavar="THRESHOLD", default=CELLTHRESHOLD)

    parser.add_argument('prefix', action="store", type=str, help="Path to folder storing the relay tor logs", metavar='PREFIX', default=None)

    args = parser.parse_args()
    args.prefix = os.path.abspath(os.path.expanduser(args.prefix))

    filename = ""
    for root, dirs, files in os.walk(args.prefix):
        for name in files:
            if(".tor." in name):
                filename = args.prefix + '/' + name

    if not filename:
        print "No tor logs found, exiting"
        exit()

    run(filename, args)

def run(filename, args):

    pylab.rcParams.update({
        'backend': 'PDF',
        'font.size': 16,
        'figure.figsize': (14,4.5),
        'figure.dpi': 100.0,
        'figure.subplot.left': 0.15,
        'figure.subplot.right': 0.95,
        'figure.subplot.bottom': 0.15,
        'figure.subplot.top': 0.95,
        'grid.color': '0.1',
        'axes.grid' : True,
        'axes.titlesize' : 'small',
        'axes.labelsize' : 'small',
        'axes.formatter.limits': (-4,4),
        'xtick.labelsize' : 'small',
        'ytick.labelsize' : 'small',
        'lines.linewidth' : 2.0,
        'lines.markeredgewidth' : 0.5,
        'lines.markersize' : 10,
        'legend.fontsize' : 'x-small',
        'legend.fancybox' : False,
        'legend.shadow' : False,
        'legend.borderaxespad' : 0.5,
        'legend.numpoints' : 1,
        'legend.handletextpad' : 0.5,
        'legend.handlelength' : 1.6,
        'legend.labelspacing' : .75,
        'legend.markerscale' : 1.0,
        # turn on the following to embedd fonts; requires latex
        #'ps.useafm' : True,
        #'pdf.use14corefonts' : True,
        #'text.usetex' : True,
    })

    try: pylab.rcParams.update({'figure.max_num_figures':50})
    except: pylab.rcParams.update({'figure.max_open_warning':50})
    try: pylab.rcParams.update({'legend.ncol':1.0})
    except: pass

    lineformats="k-,r-,b-,g-,c-,m-,y-,k--,r--,b--,g--,c--,m--,y--,k:,r:,b:,g:,c:,m:,y:,k-.,r-.,b-.,g-.,c-., m-.,y-."
    lfcycle = cycle(lineformats)

    cache_name = cache_prefix + filename.rsplit('/', 1)[1] + ".pgz"
    if not os.path.exists(cache_name.rsplit('/', 1)[0]):
        os.makedirs(cache_name.rsplit('/', 1)[0])

    if os.path.isfile(os.getcwd() + '/' + cache_name):
        print "reading cached data, should plot pretty quickly"
        data = load_zipped_pickle(cache_name)
    else:
        data = {}
        print "no cached data found, this may be slow..."

        fd = open(filename)
        line = fd.readline()

        while line:
            if "mt_ideal: queue" in line:
                content = line.split(" ", 8)[-1][:-1]
                parts = content.split(" : ")

                status = "premium" if int(parts[2]) else "nonprem"

                name = status + " " + parts[3]
                time = float(parts[0]) + float(parts[1]) / 1e6 - UNIXSTARTTIME
                cells = int(parts[4])

                if name not in data:
                    data[name] = {'time': [], 'cells':[]}
                data[name]['time'] += [time]
                data[name]['cells'] += [cells]

            line = fd.readline()

        fd.close()
        print "finished parsing file"

    save_zipped_pickle(data, cache_name)

    # parse through data to construct graph

    f = pylab.figure()

    print "plotting data"

    for name in data:
        if len(data[name]['time']) > CELLTHRESHOLD:
            x,y = [], []
            for i in range(len(data[name]['time'])):
                if data[name]['time'][i] > args.start and data[name]['time'][i] < args.end:
                    x += [data[name]['time'][i]]
                    y += [data[name]['cells'][i]]

            if "premium" in name and not args.nonprem:
                pylab.step(x, y, label=name)

            if "nonprem" in name and not args.premium:
                pylab.step(x, y, label=name)


    pylab.xlabel("time (sec)")
    pylab.ylabel("Cells in Queue")
    pylab.legend(loc="upper right")

    page = PdfPages("queue.profile.pdf")
    page.savefig()
    page.close()

if __name__ == '__main__': sys.exit(main())
