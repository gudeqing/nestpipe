import os
import sys
import argparse
from pprint import pprint
import configparser
import shutil
from nestpipe.nestpipe import RunCommands, set_logger
import time


class Basic(object):
    def __init__(self, workflow_arguments):
        self.logger = None
        self.wf_state = (0, 0)
        self.new_dirs = dict()
        workflow_arguments = self.arg_preprocess(workflow_arguments)
        # self.do_some_pre_judge(workflow_arguments)
        if workflow_arguments.arg_cfg:
            self.arg_pool = configparser.ConfigParser(interpolation=configparser.ExtendedInterpolation())
            self.arg_pool.optionxform = str
            self.arg_pool.read(workflow_arguments.arg_cfg, encoding='utf-8')
        else:
            self.arg_pool = None
        self.project_dir = workflow_arguments.o
        self.workflow_arguments = workflow_arguments
        self.workflow = self.init_workflow_dict()

    def close_logger(self):
        handlers = self.logger.handlers[:]
        for handler in handlers:
            handler.close()
            self.logger.removeHandler(handler)

    def arg_preprocess(self, arguments):
        # get script path
        script_path = os.path.abspath(arguments.script_path)
        if os.path.islink(script_path):
            script_path = os.readlink(script_path)

        project_dir = arguments.o
        if arguments.only_show_steps or arguments.only_show_detail_steps \
                or arguments.list_cmd_names or arguments.show_cmd_example:
            if arguments.pipeline_cfg is None and (not arguments.continue_run):
                # the project dir will be deleted after showing help
                project_dir = project_dir + '.tmp'

        if not os.path.exists(project_dir):
            os.mkdir(project_dir)
        project_dir = os.path.abspath(project_dir)
        arguments.o = project_dir
        time_stamp = time.strftime("%Y%m%d%H%M%S", time.localtime())
        log_file = os.path.join(project_dir, 'run.{}.log'.format(time_stamp))
        self.logger = set_logger(log_file, 'pipeline')

        if not arguments.arg_cfg:
            if not arguments.pipeline_cfg:
                arg_file = os.path.join(os.path.dirname(script_path), 'arguments.ini')
                if not arguments.only_show_steps and (not arguments.only_show_detail_steps) \
                        and (not arguments.show_cmd_example) and (not arguments.list_cmd_names):
                    self.logger.warning("You are using unchanged argument configuration: {}".format(arg_file))
                    shutil.copyfile(arg_file, os.path.join(project_dir, 'arguments.ini'))
                    arg_file = os.path.join(project_dir, 'arguments.ini')
                arguments.arg_cfg = arg_file

        with open("cmd.{}.txt".format(time_stamp), 'w') as f:
            f.write('python ' + ' '.join(sys.argv) + '\n')
            # print(sys.argv)
            f.write('>>>Argument Detail\n')
            f.write('{}\n'.format(dict(arguments.__dict__.items())))
        return arguments

    def do_some_pre_judge(self, arguments):
        if arguments.show_cmd_example:
            self.show_cmd_example(arguments.show_cmd_example)
            self.close_logger()
            shutil.rmtree(self.project_dir)
            return True

        if arguments.list_cmd_names:
            self.list_cmd_names()
            self.close_logger()
            shutil.rmtree(self.project_dir)
            return True

        if arguments.continue_run:
            if not arguments.pipeline_cfg:
                if len(os.listdir(self.project_dir)) <= 2:
                    self.close_logger()
                    shutil.rmtree(self.project_dir)
                raise Exception('Existed pipeline_cfg must be provided !')
            self.run_existed_pipeline(steps=arguments.rerun_steps)
            return True
        else:
            if arguments.pipeline_cfg:
                self.logger.warning('You are running the whole existed pipeline')
                self.run_existed_pipeline()
                return True

        if not arguments.continue_run or arguments.pipeline_cfg is None:
            if not arguments.arg_cfg:
                if len(os.listdir(self.project_dir)) <= 2:
                    self.close_logger()
                    shutil.rmtree(self.project_dir)
                raise Exception('-arg_cfg is needed!')
            if not os.path.exists(arguments.arg_cfg):
                if len(os.listdir(self.project_dir)) <= 2:
                    self.close_logger()
                    shutil.rmtree(self.project_dir)
                raise Exception('arg_cfg file not exist')

    def init_workflow_dict(self):
        commands = configparser.ConfigParser()
        commands.optionxform = str
        commands['mode'] = dict(
            threads=self.workflow_arguments.threads,
            retry=self.workflow_arguments.retry,
            monitor_resource=not self.workflow_arguments.no_monitor_resource,
            monitor_time_step=self.workflow_arguments.monitor_time_step,
            check_resource_before_run=not self.workflow_arguments.no_check_resource_before_run,
        )
        return commands

    def cmd_dict(self, cmd, **kwargs):
        args = dict(
            retry=self.workflow_arguments.retry,
            monitor_resource=not self.workflow_arguments.no_monitor_resource,
            monitor_time_step=self.workflow_arguments.monitor_time_step,
            check_resource_before_run=not self.workflow_arguments.no_check_resource_before_run,
            cpu=1, mem=1024 * 1024 * 1024,
            cmd=cmd
        )
        if kwargs:
            args.update(kwargs)
        return args

    # @staticmethod
    def mkdir(self, path, delay=True, step_name=None, exist_ok=True):
        # 为了避免非必要（比如skip导致的）的目录创建过程，增加推迟创建目录的功能delay
        # step_name的参数是为了跳过步骤时使用
        if not delay:
            if not os.path.exists(path):
                os.makedirs(path, exist_ok=exist_ok)
        else:
            # 延迟创建目录时,要求带入所属步骤名, 且每个步骤只能对应一个需要创建的目录，这个目录通常是该步骤的结果目录
            if step_name is None:
                raise Exception('必须输入有效的step_name')
            if step_name not in self.new_dirs:
                self.new_dirs[step_name] = path
            else:
                raise Exception(f'出现了一个步骤{step_name}需要创建多个目录的需求?')

    @staticmethod
    def parse_fastq_info(fastq_info_file) -> dict:
        """
        解析fastq输入信息
        :param fastq_info_file:
        format example, at least two column, the third column is optional
        '''
        sample_name | Read1_1_abspath;Read1_2_abspath | Read2_1_abspath;Read2_2_abspath
        sample_name | Read1_abspath | Read2_abspath
        '''
        :return: dict
        """
        fastq_info = dict()
        with open(fastq_info_file) as f:
            for line in f:
                if line.startswith('#') or (not line.strip()):
                    pass
                tmp_list = line.strip().split()
                sample, fqs = tmp_list[0], tmp_list[1:]
                fastq_info.setdefault(sample, list())
                read1_list = [x.strip() for x in fqs[0].split(';')]
                fastq_info[sample].append(read1_list)
                if len(fqs) >= 2:
                    read2_list = [x.strip() for x in fqs[1].split(';')]
                    fastq_info[sample].append(read2_list)
        return fastq_info

    def run_existed_pipeline(self, steps=''):
        if self.workflow_arguments.pipeline_cfg is None or not os.path.exists(self.workflow_arguments.pipeline_cfg):
            raise Exception('Please provide valid pipeline.ini file')
        workflow = RunCommands(
            self.workflow_arguments.pipeline_cfg,
            draw_state_graph=self.workflow_arguments.plot,
            outdir=self.project_dir, logger=self.logger,
            timeout=self.workflow_arguments.wait_resource_time
        )

        if self.workflow_arguments.only_show_steps:
            pprint('----Pipeline has the following main steps----')
            tmp_list = [x.split('_', 1)[0] for x in workflow.names()]
            main_steps = list()
            _ = [main_steps.append(x) for x in tmp_list if x not in main_steps]
            pprint(main_steps)
            return
        elif self.workflow_arguments.only_show_detail_steps:
            pprint('----Pipeline has the following steps----')
            pprint(workflow.names())
            return
        if self.workflow_arguments.continue_run:
            workflow.continue_run(steps=steps)
        else:
            workflow.parallel_run()

    def show_cmd_example(self, cmd_name):
        """
        :param cmd_name: cmd_generator中的函数名,也是arguments.ini中的section名
        :return: None
        """
        print('This function should be overrided')
        # if not self.workflow_arguments.arg_cfg:
        #     raise Exception("please first input arg_cfg ")
        # if cmd_name not in self.arg_pool:
        #     raise Exception('please provide valid cmd_name, refer --list_cmd_names')
        # exec("print(cmdx.{}(**self.arg_pool['{}']))".format(cmd_name, cmd_name))

    def list_cmd_names(self):
        if not self.workflow_arguments.arg_cfg:
            raise Exception("please first input arg_cfg ")
        print(list(self.arg_pool.keys())[1:])

    def skip_some_steps(self):
        commands = self.workflow
        skip_steps = self.workflow_arguments.skip
        project_dir = self.project_dir
        if skip_steps:
            with open(os.path.join(project_dir, 'pipeline.ini'), 'w') as configfile:
                commands.write(configfile)
            workflow = RunCommands(os.path.join(project_dir, 'pipeline.ini'),
                                   draw_state_graph=self.workflow_arguments.plot,
                                   outdir=project_dir, logger=self.logger,
                                   timeout=self.workflow_arguments.wait_resource_time)
            skip_detail = list()
            for each in skip_steps:
                skips = [x for x in commands if x == each or x.startswith(each + '_')]
                skip_detail.extend(skips)
                if not skips:
                    exit('Step {} was Not found, please refer --only_show_steps or --only_show_detail_steps'.format(
                        each))
                else:
                    _ = [commands.pop(x) for x in skips]
            # skip the step whose dependencies are not all included in the commands
            total_deduced_skips = list()
            while True:
                to_be_skip = list()
                for step in commands.keys():
                    depends = workflow.get_dependency(step)
                    if set(depends) - set(commands.keys()):
                        # print('Skip {} for at least one of its dependencies were skipped'.format(step))
                        to_be_skip.append(step)
                        total_deduced_skips.append(step)
                _ = [commands.pop(x) for x in to_be_skip]
                if all(len(set(workflow.get_dependency(x)) - set(commands.keys())) == 0 for x in commands):
                    break
            if total_deduced_skips:
                self.logger.warning("Warning: the following steps are also skipped for depending relationship")
                self.logger.warning(set(x.split('_', 1)[0] for x in total_deduced_skips))

            # 删除由于跳过一些步骤产生的无用目录, 但由于目录组织结构由编写pipeline的人决定,
            # 下面的代码不一定能删除所有无用的目录, 甚至有可能删除必要的目录
            all_skipped_detail = skip_detail + total_deduced_skips
            # 下面的代码避免了要跳过的步骤所需要的目录
            self.new_dirs = {k: v for k, v in self.new_dirs.items() if k not in all_skipped_detail}
            for each in all_skipped_detail:
                if '_' not in each:
                    possible_dir = os.path.join(self.project_dir, each)
                else:
                    parent, child = each.split('_', 1)
                    possible_dir = os.path.join(self.project_dir, parent, child)
                if os.path.exists(possible_dir):
                    if not os.listdir(possible_dir):
                        os.rmdir(possible_dir)
                    else:
                        has_no_file = True
                        for root, dirs, files in os.walk(possible_dir):
                            if files:
                                has_no_file = False
                                break
                        if has_no_file:
                            shutil.rmtree(possible_dir)
            # 删除跳过的具体步骤所需的目录后，检查大步骤所需的目录是否为空，如果为空，也要删除
            all_skipped_main = set(x.split('_', 1)[0] for x in all_skipped_detail)
            self.new_dirs = {k: v for k, v in self.new_dirs.items() if k not in all_skipped_main}
            for each in all_skipped_main:
                possible_dir = os.path.join(self.project_dir, each)
                if os.path.exists(possible_dir):
                    if not os.listdir(possible_dir):
                        os.rmdir(possible_dir)

    def run(self):
        self.skip_some_steps()
        commands = self.workflow
        arguments = self.workflow_arguments
        project_dir = self.project_dir
        tmp_list = [x.split('_', 1)[0] for x in commands.keys()]
        main_steps = list()
        _ = [main_steps.append(x) for x in tmp_list if x not in main_steps]
        if arguments.only_show_steps:
            pprint('----Pipeline has the following main steps----')
            pprint(main_steps[2:])
            self.close_logger()
            shutil.rmtree(project_dir)
            return

        elif arguments.only_show_detail_steps:
            pprint('----Pipeline has the following steps----')
            pprint(list(commands.keys())[2:])
            self.close_logger()
            shutil.rmtree(project_dir)
            return

        # 在程序运行前, 创建被延迟创建的目录
        for step, path in self.new_dirs.items():
            self.mkdir(path, delay=False)

        # ---------write pipeline cmds--------------------
        with open(os.path.join(project_dir, 'pipeline.ini'), 'w') as configfile:
            commands.write(configfile)
        if arguments.only_write_pipeline:
            return

        # ----------------run-----------------
        workflow = RunCommands(os.path.join(project_dir, 'pipeline.ini'),
                               logger=self.logger,
                               outdir=project_dir,
                               draw_state_graph=arguments.plot,
                               timeout=arguments.wait_resource_time)
        self.wf_state = workflow.parallel_run()


def basic_arg_parser():
    if len(sys.argv) <= 1:
        exit('Please provide at least one argument, use -h for help')
    parser = argparse.ArgumentParser()
    parser.add_argument('-arg_cfg', required=False, help="参数配置文件, 包含流程所有软件的需要的参数, "
                                                         "默认使用该脚本当前目录的argument.ini")
    parser.add_argument('-o', default=os.path.join(os.getcwd(), 'Result'), help='分析目录或结果目录')
    parser.add_argument('-skip', default=list(), nargs='+',
                        help='指定要跳过的步骤名, 空格分隔,程序会自动跳过依赖他们的步骤, --only_show_steps可查看步骤名; '
                             '注意: 对于rnaseq_pipeline, 如果跳过trim步骤, 则用原始数据进行后续分析; '
                             '如果跳过assembl或mergeTranscript, 则仅对参考基因定量')
    parser.add_argument('--only_show_steps', default=False, action="store_true",
                        help="仅仅显示当前流程包含的主步骤, 且已经排除指定跳过的步骤; "
                             "你还可以通过--list_cmd_names查看当前流程包含哪些命令行")
    parser.add_argument('--only_show_detail_steps', default=False, action="store_true",
                        help="仅仅显示当前流程包含的详细步骤, 且已经排除指定跳过的步骤")
    parser.add_argument('--only_write_pipeline', default=False, action='store_true',
                        help="仅仅生成流程pipeline.ini")
    parser.add_argument('-threads', default=5, type=int, help="允许的最大并行的cmd数目, 默认5")
    parser.add_argument('-retry', default=1, type=int,
                        help='某步骤运行失败后再尝试运行的次数, 默认1次. 如需对某一步设置不同的值, 可在运行流程前修改pipeline.ini')
    parser.add_argument('--continue_run', default=False, action='store_true',
                        help='流程运行结束后, 从失败的步骤续跑, 记得要用-o指定之前的结果目录, 用-pipeline_cfg指定pipeline.ini; '
                             '如果顺便还想重新跑已经成功运行的步骤, 可通过-rerun_steps指定, 或者在状态表cmd_stat.txt中将其修改为failed即可')
    parser.add_argument('-rerun_steps', default=list(), nargs='+',
                        help="使用--continue_run有效, 通过该参数指定重跑已经成功的步骤, 空格分隔, 这样做的可能原因可以是: 你重新设置了参数")
    parser.add_argument('-pipeline_cfg', default=None,
                        help="已有的pipeline.ini, 续跑时必须需提供; "
                             "如提供该参数, 则此时无视 arg_cfg,fastq_info,group,cmp,skip 等用于生成项目流程的参数")
    parser.add_argument('--list_cmd_names', default=False, action='store_true', help="仅输出参数配置文件里的包含的cmd名称")
    parser.add_argument('-show_cmd_example', help="提供一个cmd名称,输出该cmd的样例")
    parser.add_argument('--no_monitor_resource', default=False, action='store_true',
                        help='是否监控每一步运行时的资源消耗, 如需对某一步设置不同的值, 可在运行流程前修改pipeline.ini')
    parser.add_argument('--monitor_time_step', default=3, type=int,
                        help='监控资源时的时间间隔, 默认3秒, 如需对某一步设置不同的值, 可在运行流程前修改pipeline.ini')
    parser.add_argument('-wait_resource_time', default=1500, type=int,
                        help="等待资源的时间上限, 默认1500秒, 等待时间超过这个时间时,资源不足时判定任务失败")
    parser.add_argument('--no_check_resource_before_run', default=False, action='store_true',
                        help="指示运行某步骤前检测指定的资源是否足够, 如不足, 则该步骤失败; 如果设置该参数, 则运行前不检查资源. "
                             "如需对某一步设置不同的值,可运行前修改pipeline.ini. "
                             "如需更改指定的资源, 可在运行流程前修改pipeline.ini")
    parser.add_argument('--plot', action='store_true', default=False,
                        help="if set, running state will be visualized if pygraphviz installed")
    return parser
