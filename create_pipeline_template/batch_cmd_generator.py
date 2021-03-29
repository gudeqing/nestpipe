import os
import sys
from nestpipe.workflow_basic import Basic
import create_pipeline_template.one_cmd_generator as cmdx


class NestedCmd(Basic):
    def __init__(self, workflow_arguments):
        super().__init__(workflow_arguments)
        terminate = self.do_some_pre_judge(workflow_arguments)
        if terminate:
            exit(0)
        # self.arg_pool is from Basic after processing workflow_arguments
        # self.cmd_dict is from Basic after processing workflow_arguments
        # self.project_dir is from Basic after processing workflow_arguments
        # self.workflow is from Basic after processing workflow_arguments

    def show_cmd_example(self, cmd_name):
        """
        :param cmd_name: cmd_generator中的函数名,也是arguments.ini中的section名
        :return: None
        """
        if not self.workflow_arguments.arg_cfg:
            raise Exception("please first input arg_cfg ")
        if cmd_name not in self.arg_pool:
            raise Exception('please provide valid cmd_name, refer --list_cmd_names')
        exec("print(cmdx.{}(**self.arg_pool['{}']))".format(cmd_name, cmd_name))

    # 对于当前模板,你只需要从下开始修改代码,即按照规律逐一增加自己的函数，每个函数用来批量生成一组cmds, 并且囊括了其依赖的cmd信息
    # 此类的函数没有具体的写法，但必须：
    # 1.return一个字典，其包含当前步骤设计的所有cmd信息，如{'step_name':{cmd='echo xx', cpu=2, out_file='xxx'}}
    # 2.执行self.workflow.update， 这个self.workflow字典包含所有步骤的所有cmd信息
    # 下面有两个示例
    def which_cmds(self, tool_name, step_name='1.BasicStat', main_step_name='1.QC'):
        # step_name是步骤名，如果你希望该步骤的结果出现在另外一个步骤的子目录下，可以增加main_step_name参数
        commands = dict()
        outdir = os.path.join(self.project_dir, main_step_name, step_name)
        self.mkdir(outdir, exist_ok=True)
        # 1. get args from pool
        args = dict(self.arg_pool[tool_name])
        # 2. update args
        args['prefix'] = os.path.join(outdir, 'all')
        # 3. format cmds
        commands[step_name] = self.cmd_dict(
            cmd=cmdx.which(**args),
            cpu=2,
            mem=1024 ** 3 * 1,
            metadata=args['metadata'],
            out_prefix=args['prefix'],
        )
        # 4. update workflow
        self.workflow.update(commands)
        return commands

    def which2_cmds(self, depend_cmds, step_name='3.VJ-pair-bar3d', main_step_name='3.VJ-Usage'):
        # 这里depend_cmds是当前步骤所依赖的cmd信息，包含了所有样本的cmd信息，所以下面需要循环处理
        commands = dict()
        outdir = os.path.join(self.project_dir, main_step_name, step_name)
        self.mkdir(outdir, exist_ok=True)
        for step, cmd_info in depend_cmds.items():
            sample = cmd_info['sample']
            args = dict(self.arg_pool['Plot3dVJUsage'])
            args['data'] = cmd_info['out_prefix'] + '.fancyvj.wt.txt'
            args['out'] = os.path.join(outdir, sample + '.VJ.3dBar.png')
            cmd = cmdx.which2(**args)
            commands[step_name+'_'+sample] = self.cmd_dict(
                depend=step,
                cmd=cmd,
                cpu=2,
                mem=1024 ** 3 * 1,
                sample=sample,
                out=args['out']
            )
        self.workflow.update(commands)
        return commands
