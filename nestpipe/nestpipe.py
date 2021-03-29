# coding=utf-8
__author__ = 'gudeqing'
import time
import os
import configparser
import psutil
import queue
import logging
from subprocess import PIPE
import threading
from threading import Timer, Lock
import weakref
import atexit
import signal

try:
    import pygraphviz as pgv
except Exception as e:
    print('Warn: cannot import pygraphviz and state graph will not be drawn!')
    pgv = None

PROCESS_local = weakref.WeakKeyDictionary()
PROCESS_remote = weakref.WeakKeyDictionary()


@atexit.register
def _kill_processes_when_exit():
    print("....Ending....")
    for proc, cmd_name in PROCESS_local.items():
        if psutil.pid_exists(proc.pid):
            print('Shutting down running tasks {}:{}'.format(proc.pid, cmd_name))
            proc.kill()
    # 有些已经发起但还没有收进来的无法终止
    for proc in list(PROCESS_remote.keys()):
        cmd_name = PROCESS_remote[proc]
        if proc.pid_exists:
            print('Shutting down remote running tasks {}:{}'.format(proc.pid, cmd_name))
            proc.kill()


def shutdown(signum, frame):
    print('Killing main process, thus its derived processes will also be killed')
    exit(0)


# kill signal will be captured
signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)


def set_logger(name='workflow.log', logger_id='x'):
    logger = logging.getLogger(logger_id)
    logger.propagate = False
    logger.setLevel(logging.INFO)
    fh = logging.FileHandler(name, mode='w+')
    fh.setLevel(logging.INFO)
    sh = logging.StreamHandler()
    sh.setLevel(logging.WARNING)
    # fmt = '%(asctime)s - %(pathname)s[line:%(lineno)d] - %(levelname)s: %(message)s'
    fmt = '%(asctime)s: %(message)s'
    format_str = logging.Formatter(fmt)  # 设置日志格式
    fh.setFormatter(format_str)
    logger.addHandler(sh)
    logger.addHandler(fh)
    return logger


class Command(object):
    def __init__(self, cmd, name, timeout=3600*24*10, outdir=os.getcwd(),
                 monitor_resource=True, monitor_time_step=2, logger=None, **kwargs):
        self.name = name
        self.cmd = cmd
        self.proc = None
        self.stdout = None
        self.stderr = None
        self.timeout = int(timeout)
        self.used_time = 0
        self.max_mem = 0
        self.max_cpu = 0
        self.monitor = monitor_resource
        self.monitor_time_step = int(monitor_time_step)
        self.outdir = outdir
        if not logger:
            self.logger = set_logger(name=os.path.join(self.outdir, 'command.log'))
        else:
            self.logger = logger

    def _monitor_resource(self):
        while self.proc.is_running():
            try:
                # if os.name == 'posix':
                #     cpu_num = self.proc.cpu_num()
                # elif os.name == 'nt':
                #     cpu_num = psutil.cpu_count()
                # else:
                #     cpu_num = 0
                cpu_percent = self.proc.cpu_percent(self.monitor_time_step)
                used_cpu = round(cpu_percent*0.01, 4)
                if used_cpu > self.max_cpu:
                    self.max_cpu = used_cpu
                memory_obj = self.proc.memory_full_info()
                # memory = (memory_obj.vms - memory_obj.shared)/1024/1024
                # memory = round(memory_obj.vms/1024/1024, 4)
                memory = round(memory_obj.uss/1024/1024, 4)
                if memory > self.max_mem:
                    self.max_mem = memory
            except Exception as e:
                # print('Failed to capture cpu/mem info for: ', e)
                break

    def run(self):
        start_time = time.time()
        self.logger.warning("RunStep: {}".format(self.name))
        self.logger.info("RunCmd: {}".format(self.cmd))
        # submit task
        self.proc = psutil.Popen(self.cmd, shell=True, stderr=PIPE, stdout=PIPE)
        PROCESS_local[self.proc] = self.name
        if self.monitor:
            thread = threading.Thread(target=self._monitor_resource, daemon=True)
            thread.start()
        timer = Timer(self.timeout, self.proc.kill)
        try:
            timer.start()
            self.stdout, self.stderr = self.proc.communicate()
            if self.monitor:
                thread.join()
        finally:
            timer.cancel()
        self._write_log()
        end_time = time.time()
        self.used_time = round(end_time - start_time, 4)

    def _write_log(self):
        log_dir = os.path.join(self.outdir, 'logs')
        if not os.path.exists(log_dir):
            try:
                os.mkdir(log_dir)
            except FileExistsError:
                pass

        prefix = os.path.join(self.outdir, 'logs', self.name+'.'+str(self.proc.pid))
        if self.stderr:
            with open(prefix+'.stderr.txt', 'wb') as f:
                f.write(self.stderr)
        if self.stdout:
            with open(prefix+'.stdout.txt', 'wb') as f:
                f.write(self.stdout)
        if self.max_cpu or self.max_mem:
            with open(prefix+'.resource.txt', 'w') as f:
                f.write('max_cpu: {}\n'.format(self.max_cpu))
                f.write('max_mem: {}M\n'.format(round(self.max_mem, 4)))


class CommandNetwork(object):
    def __init__(self, cmd_config):
        # self.parser = configparser.ConfigParser()
        self.parser = configparser.ConfigParser(interpolation=configparser.ExtendedInterpolation())
        self.parser.read(cmd_config, encoding='utf-8')
        self.pool_size = self.parser.getint('mode', 'threads')

    def names(self):
        sections = self.parser.sections()
        # mode section is not cmd
        sections.pop(sections.index('mode'))
        return sections

    def orphans(self):
        independent_cmds = list()
        for name in self.names():
            if 'depend' not in self.parser[name]:
                independent_cmds.append(name)
            else:
                depend = self.parser[name]['depend'].strip()
                if not depend:
                    independent_cmds.append(name)
                else:
                    for each in depend.split(','):
                        if each not in self.names():
                            raise Exception(f'Step "{each}" is not in your pipeline! A spelling mistake?')
        return independent_cmds

    def get_dependency(self, name):
        if 'depend' not in self.parser[name]:
            return []
        else:
            depend = self.parser[name]['depend'].strip()
            if not depend:
                return []
            else:
                return [x.strip() for x in depend.split(',')]

    def get_cmd_description_dict(self, name):
        tmp_dict = dict(self.parser[name])
        tmp_dict['name'] = name
        if 'cpu' not in tmp_dict:
            tmp_dict['cpu'] = 0
        if 'mem' not in tmp_dict:
            tmp_dict['mem'] = 0
        if 'depend' not in tmp_dict:
            tmp_dict['depend'] = None
        if 'retry' not in tmp_dict:
            tmp_dict['retry'] = self.parser.getint('mode', 'retry')
        else:
            tmp_dict['retry'] = self.parser.getint(name, 'retry')
        if 'monitor_resource' not in tmp_dict:
            tmp_dict['monitor_resource'] = self.parser.getboolean('mode', 'monitor_resource')
        else:
            tmp_dict['monitor_resource'] = self.parser.getboolean(name, 'monitor_resource')
        if 'timeout' not in tmp_dict:
            tmp_dict['timeout'] = 3600*24*10
        else:
            tmp_dict['timeout'] = self.parser.getint(name, 'timeout')
        if 'monitor_time_step' not in tmp_dict:
            tmp_dict['monitor_time_step'] = self.parser.getint('mode', 'monitor_time_step')
        else:
            tmp_dict['monitor_time_step'] = self.parser.getint(name, 'monitor_time_step')
        if 'check_resource_before_run' not in tmp_dict:
            tmp_dict['check_resource_before_run'] = self.parser.getboolean('mode', 'check_resource_before_run')
        else:
            tmp_dict['check_resource_before_run'] = self.parser.getboolean(name, 'check_resource_before_run')
        return tmp_dict


class CheckResource(object):
    @staticmethod
    def available_mem():
        return psutil.virtual_memory().free

    @staticmethod
    def available_cpu():
        total = psutil.cpu_count()
        return int(total - total*psutil.cpu_percent()*0.01)

    def is_enough(self, cpu, mem, timeout=10):
        start_time = time.time()
        enough_num = 0
        while True:
            if float(cpu) <= self.available_cpu() \
                    and float(mem) <= self.available_mem():
                enough_num += 1
                if enough_num >= 3:
                    return True
                if enough_num >= 1 and timeout <= 10:
                    return True
            if time.time() - start_time >= timeout:
                return False
            time.sleep(3)


class StateGraph(object):
    def __init__(self, state):
        self.state = state
        self.graph = pgv.AGraph(directed=True, rankdir='LR')
        self.used_colors = dict()
        self.color_dict = dict(
            success='#7FFF00',
            failed='#FFD700',
            running='#9F79EE',
            queueing='#87CEFF',
            killed='red',
            outdoor='#A8A8A8',
        )

    def _add_nodes(self):
        for node, cmd_info in self.state.items():
            status = cmd_info['state']
            node_detail = node.split('_', 1)
            if status in self.color_dict:
                color = self.color_dict[status]
            else:
                color = '#A8A8A8'
            self.used_colors[status] = color
            used_time = cmd_info['used_time']
            if isinstance(used_time, str):
                if used_time == 'unknown':
                    pass
                else:
                    try:
                        float(used_time)
                        node_detail.append(used_time+'s')
                    except ValueError:
                        node_detail.append(used_time)
            elif float(used_time) <= 0:
                pass
            else:
                node_detail.append(str(used_time) + 's')
            self.graph.add_node(
                node,
                # 谷歌浏览器可以正常显示tooltip
                tooltip=cmd_info['cmd'].replace(' ', '\n'),
                shape="box",
                style="rounded, filled",
                fillcolor=color,
                color="mediumseagreen",
                label='\n'.join(node_detail)
            )

    def _add_edges(self):
        for target in self.state:
            sources = self.state[target]['depend'].strip()
            if sources:
                sources = sources.split(',')
                edges = zip(sources, [target]*len(sources))
                if self.state[target]['state'] == 'success':
                    color = 'green'
                elif self.state[target]['state'] == 'running':
                    color = '#836FFF'
                else:
                    color = '#4D4D4D'
                self.graph.add_edges_from(edges, color=color)
            else:
                self.graph.add_edge('Input', target, color='green')

    def _add_legend(self):
        subgraph = self.graph.add_subgraph(name='cluster_sub', label='Color Legend')
        subgraph.graph_attr['color'] = 'lightgrey'
        subgraph.graph_attr['style'] = 'filled'
        subgraph.graph_attr['ratio'] = 'compress'
        for node, color in self.used_colors.items():
            subgraph.add_node(
                node,
                shape="note",
                style="filled",
                fillcolor=color,
                color="mediumseagreen",
            )
        nodes = list(self.used_colors.keys())
        for ind in range(len(nodes)):
            if ind <= len(nodes) - 2:
                subgraph.add_edge(nodes[ind], nodes[ind+1], style='invis')

    def draw(self, img_file='state.svg'):
        self._add_nodes()
        self._add_edges()
        self._add_legend()
        img_fmt = os.path.splitext(img_file)[1][1:]
        self.graph.draw(path=img_file, format=img_fmt, prog='dot')


class RunCommands(CommandNetwork):
    __LOCK__ = Lock()

    def __init__(self, cmd_config, outdir=os.getcwd(), timeout=10, logger=None, draw_state_graph=True):
        super().__init__(cmd_config)
        self.end = False
        self.ever_queued = set()
        self.queue = self.__init_queue()
        self.state = self.__init_state()
        self.task_number = len(self.state)
        self.outdir = outdir
        self.success = 0
        self.failed = 0
        # wait resource time limit
        self.timeout = timeout
        if not logger:
            self.logger = set_logger(name=os.path.join(self.outdir, 'workflow.log'))
        else:
            self.logger = logger
        # draw state graph
        self.draw_state_graph = draw_state_graph if pgv else False

    def __init_queue(self):
        cmd_pool = queue.Queue()
        for each in self.orphans():
            cmd_pool.put(each)
            self.ever_queued.add(each)
        return cmd_pool

    def __init_state(self):
        state_dict = dict()
        for name in self.names():
            state_dict[name] = dict()
            fields = ['state', 'used_time', 'mem', 'cpu', 'pid', 'depend', 'cmd']
            for each in fields:
                state_dict[name][each] = 'unknown'
            state_dict[name]['cmd'] = self.parser[name]['cmd']
            state_dict[name]['depend'] = ','.join(self.get_dependency(name))
        return state_dict

    def _update_queue(self):
        success = set(x for x in self.state if self.state[x]['state'] == 'success')
        failed = set(x for x in self.state if self.state[x]['state'] == 'failed')
        waiting = set(self.names()) - self.ever_queued
        if not waiting:
            self.queue.put(None)
        for each in waiting:
            dependency = set(self.get_dependency(each))
            if dependency & failed:
                self.ever_queued.add(each)
                self.state[each]['state'] = 'failed'
                self.state[each]['used_time'] = 'FailedDependencies'
                self.logger.warning(each + ' cannot be started for some failed dependencies!')
            if not (dependency - success):
                self.ever_queued.add(each)
                self.queue.put(each, block=True)

    def _update_state(self, cmd=None, killed=False):
        if cmd is not None:
            cmd_state = self.state[cmd.name]
            if cmd.proc is None:
                cmd_state['state'] = 'failed'
                cmd_state['used_time'] = 'NotEnoughResource'
                self.logger.warning(cmd.name + ' cannot be started for not enough resource!')
            else:
                cmd_state['state'] = 'success' if cmd.proc.returncode == 0 else 'failed'
                cmd_state['used_time'] = cmd.used_time
                cmd_state['mem'] = cmd.max_mem
                cmd_state['cpu'] = cmd.max_cpu
                cmd_state['pid'] = cmd.proc.pid
        success = set(x for x in self.state if self.state[x]['state'] == 'success')
        self.success = len(success)
        failed = set(x for x in self.state if self.state[x]['state'] == 'failed')
        self.failed = len(failed)
        running_or_queueing = self.ever_queued - success - failed
        waiting = set(self.names()) - self.ever_queued
        tmp_dict = {y: x for x, y in PROCESS_local.items()}
        tmp_dict.update({y: x for x, y in PROCESS_remote.items()})
        for each in running_or_queueing:
            try:
                if each in tmp_dict:
                    self.state[each]['pid'] = tmp_dict[each].pid
                    if tmp_dict[each].is_running():
                        if killed:
                            self.state[each]['state'] = 'killed'
                        else:
                            self.state[each]['state'] = 'running'
                else:
                    self.state[each]['state'] = 'queueing'
            except Exception as e:
                pass
        for each in waiting:
            self.state[each]['state'] = 'outdoor'

    def _write_state(self):
        outfile = os.path.join(self.outdir, 'cmd_state.txt')
        if time.localtime().tm_min % 5 == 0:
            back_file = os.path.join(self.outdir, 'bak.cmd_state.txt')
            if os.path.exists(outfile):
                os.rename(outfile, back_file)
        with open(outfile, 'w') as f:
            fields = ['name', 'state', 'used_time', 'mem', 'cpu', 'pid', 'depend', 'cmd']
            f.write('\t'.join(fields)+'\n')
            for name in self.state:
                content = '\t'.join([str(self.state[name][x]) for x in fields[1:]])
                f.write(name+'\t'+content+'\n')

    def _draw_state(self):
        if self.draw_state_graph:
            outfile = os.path.join(self.outdir, 'state.svg')
            back_file = os.path.join(self.outdir, 'bak.state.svg')
            if os.path.exists(outfile):
                os.rename(outfile, back_file)
            StateGraph(self.state).draw(outfile)

    def _update_status_when_exit(self):
        # print('final update status')
        self._update_state(killed=True)
        self._write_state()
        self._draw_state()

    def single_run(self):
        while True:
            if self.queue.empty():
                time.sleep(5)
                with self.__LOCK__:
                    self._update_queue()
                    self._write_state()
                    self._draw_state()
                continue
            name = self.queue.get(block=True)
            if name is None:
                self.queue.put(None)
                self.end = True
                break
            tmp_dict = self.get_cmd_description_dict(name)
            if 'outdir' in tmp_dict:
                tmp_dict.pop('outdir')
            if 'logger' in tmp_dict:
                tmp_dict.pop('logger')
            try_times = 0
            cmd = Command(**tmp_dict, outdir=self.outdir, logger=self.logger)
            while try_times <= int(tmp_dict['retry']):
                try_times += 1
                enough = True
                if tmp_dict['check_resource_before_run']:
                    if not CheckResource().is_enough(tmp_dict['cpu'], tmp_dict['mem'], self.timeout):
                        self.logger.warning('Local resource is Not enough for {}!'.format(cmd.name))
                        enough = False
                if enough:
                    if try_times > 1:
                        self.logger.warning('{}th run {}'.format(try_times, cmd.name))
                    self.state[cmd.name]['state'] = 'running'
                    with self.__LOCK__:
                        self._draw_state()
                    cmd.run()
                    if cmd.proc.returncode == 0:
                        break
            with self.__LOCK__:
                self._update_state(cmd)
                self._update_queue()
                self._write_state()
                self._draw_state()

    def parallel_run(self):
        atexit.register(self._update_status_when_exit)
        pool_size = self.parser.getint('mode', 'threads')
        threads = list()
        for _ in range(pool_size):
            thread = threading.Thread(target=self.single_run, daemon=True)
            threads.append(thread)
            thread.start()

        # update state
        time.sleep(2)
        with self.__LOCK__:
            self._update_state()
            self._write_state()
            self._draw_state()
        # join threads
        _ = [x.join() for x in threads]
        self.logger.warning('Finished all tasks!')
        self.logger.warning('Success/Total = {}/{}'.format(self.success, self.task_number))
        return self.success, len(self.state)

    def continue_run(self, steps=''):
        detail_steps = []
        if steps:
            for each in steps:
                detail_steps += [x for x in self.names() if x == each or x.startswith(each + '_')]

        self.ever_queued = set()
        # 使用已有状态信息更新状态
        existed_state_file = os.path.join(self.outdir, 'cmd_state.txt')
        if not os.path.exists(existed_state_file):
            raise Exception('We found no cmd_state.txt file in {}!'.format(self.outdir))
        with open(existed_state_file, 'r') as f:
            _ = f.readline()
            for line in f:
                line_lst = line.strip().split('\t')
                fields = ['state', 'used_time', 'mem', 'cpu', 'pid', 'depend', 'cmd']
                if line_lst[1] == 'success':
                    if line_lst[0] in detail_steps:
                        continue
                    self.ever_queued.add(line_lst[0])
                    # 已有的depend和cmd信息不被带入到continue运行模式, 给续跑功能带来更多可能
                    if line_lst[0] in self.state:
                        self.state[line_lst[0]].update(dict(zip(fields[:-2], line_lst[1:])))
                    else:
                        self.logger.warning(line_lst[0] + ' was skipped for a modified pipeline.ini was used')
        failed = set(self.names()) - self.ever_queued
        if failed:
            self.logger.warning('Continue to run: {}'.format(failed))
        else:
            self.logger.warning('Nothing to continue run')
        self.queue = queue.Queue()
        self._update_queue()
        self._draw_state()
        self.parallel_run()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('-cfg', required=True, help="pipeline configuration file")
    parser.add_argument('-outdir', required=False, default='.', help="output directory")
    parser.add_argument('-wt', required=False, type=float, default=10,
                        help="time to wait for enough resource to initiate a task")
    parser.add_argument('--plot', action='store_true', default=False,
                        help="if set, running state will be visualized if pygraphviz installed")
    parser.add_argument('--rerun', action='store_true', default=False,
                        help="if set, restart the pipeline at the failed/broken points")
    args = parser.parse_args()
    workflow = RunCommands(args.cfg, timeout=args.wt, outdir=args.outdir, draw_state_graph=args.plot)
    if not args.rerun:
        workflow.parallel_run()
    else:
        workflow.continue_run()
