import itertools
from contextlib import contextmanager
from .base import TargetBase, is_complex_type
from ..langs.python import Module
from .. import compiler


def type_to_packer(t):
    if t == compiler.t_void:
        return "None"
    elif t == compiler.t_bool:
        return "packers.Bool"
    elif t == compiler.t_int8:
        return "packers.Int8"
    elif t == compiler.t_int16:
        return "packers.Int16"
    elif t == compiler.t_int32:
        return "packers.Int32"
    elif t == compiler.t_int64:
        return "packers.Int64"
    elif t == compiler.t_float:
        return "packers.Float"
    elif t == compiler.t_date:
        return "packers.Date"
    elif t == compiler.t_buffer:
        return "packers.Buffer"
    elif t == compiler.t_string:
        return "packers.Str"
    elif t == compiler.t_heteromap:
        return "heteroMapPacker"
    elif isinstance(t, (compiler.TList, compiler.TSet, compiler.TMap)):
        return "_%s" % (t.stringify(),)
    elif isinstance(t, (compiler.Enum, compiler.Record, compiler.Exception)):
        return "%sPacker" % (t.name,)
    elif isinstance(t, compiler.Class):
        return "%sObjRef" % (t.name,)
    return "%r$$$packer" % (t,)

def const_to_python(typ, val):
    if val is None:
        return "None"
    elif typ == compiler.t_bool:
        if val:
            return "True"
        else:
            return "False"
    elif typ == compiler.t_int8:
        return repr(val)
    elif typ == compiler.t_int16:
        return repr(val)
    elif typ == compiler.t_int32:
        return repr(val)
    elif typ == compiler.t_int64:
        return repr(val)
    elif typ == compiler.t_float:
        return repr(val)
    elif typ == compiler.t_string:
        return repr(val)
    elif isinstance(typ, compiler.TList):
        return "[%s]" % (", ".join(const_to_cs(typ.oftype, item) for item in val),)
    else:
        raise IDLError("%r cannot be converted to a python const" % (val,))


class PythonTarget(TargetBase):
    DEFAULT_TARGET_DIR = "."

    @contextmanager
    def new_module(self, filename):
        mod = Module()
        yield mod
        with self.open(filename, "w") as f:
            f.write(mod.render())

    def generate(self, service):
        with self.new_module("%s_bindings.py" % (service.name,)) as module:
            BLOCK = module.block
            STMT = module.stmt
            SEP = module.sep
            DOC = module.doc
            
            STMT("import agnos")
            STMT("from agnos import packers")
            STMT("from agnos import utils")
            STMT("from functools import partial")
            SEP()
            
            STMT("AGNOS_VERSION = '{0}'", compiler.AGNOS_VERSION)
            STMT("IDL_MAGIC = '{0}'", service.digest)
            if not service.clientversion:
                STMT("CLIENT_VERSION = None")
            else:
                STMT("CLIENT_VERSION = '{0}'", service.clientversion)
            STMT("SUPPORTED_VERSIONS = {0}", service.versions)
            SEP()
            
            DOC("enums", spacer = True)
            for member in service.types.values():
                if isinstance(member, compiler.Enum):
                    self.generate_enum(module, member)
                    SEP()
            
            DOC("records", spacer = True)
            for member in service.types.values():
                if isinstance(member, compiler.Record):
                    self.generate_record_class(module, member)
                    SEP()
            
            for member in service.types.values():
                if isinstance(member, compiler.Record):
                    if not is_complex_type(member):
                        self.generate_record_packer(module, member)
                        SEP()
            
            DOC("consts", spacer = True)
            for member in service.consts.values():
                STMT("{0} = {1}", member.name, const_to_python(member.type, member.value))
            SEP()
            
            DOC("classes", spacer = True)
            for member in service.types.values():
                if isinstance(member, compiler.Class):
                    self.generate_class_proxy(module, service, member)
                    SEP()
            SEP()
            
            DOC("server", spacer = True)
            self.generate_handler_interface(module, service)
            SEP()
            
            self.generate_processor(module, service)
            SEP()
            
            DOC("client", spacer = True)
            self.generate_client(module, service)
            SEP()

    def _generate_templated_packer_for_type(self, tp):
        if isinstance(tp, compiler.TList):
            return "packers.ListOf(%s, %s)" % (tp.id, self._generate_templated_packer_for_type(tp.oftype),)
        if isinstance(tp, compiler.TSet):
            return "packers.SetOf(%s, %s)" % (tp.id, self._generate_templated_packer_for_type(tp.oftype),)
        elif isinstance(tp, compiler.TMap):
            return "packers.MapOf(%s, %s, %s)" % (tp.id, self._generate_templated_packer_for_type(tp.keytype),
                self._generate_templated_packer_for_type(tp.valtype))
        else:
            return type_to_packer(tp)

    def generate_templated_packers(self, module, service):
        BLOCK = module.block
        STMT = module.stmt
        SEP = module.sep
        
        for tp in service.all_types:
            if isinstance(tp, (compiler.TList, compiler.TSet, compiler.TMap)):
                definition = self._generate_templated_packer_for_type(tp)
                STMT("_{0} = {1}", tp.stringify(), definition)

    def generate_enum(self, module, enum):
        BLOCK = module.block
        STMT = module.stmt
        SEP = module.sep
        
        members = ["\n    '%s' : %s" % (m.name, m.value) for m in enum.members]
        STMT("{0} = utils.create_enum('{0}', {{{1}}})", enum.name, ", ".join(members))
        SEP()
        with BLOCK("class {0}Packer(packers.Packer)", enum.name):
            STMT("@classmethod")
            with BLOCK("def get_id(cls)"):
                STMT("return {0}", enum.id)
            STMT("@classmethod")
            with BLOCK("def pack(cls, obj, stream)"):
                with BLOCK("if isinstance(obj, utils.Enum)"):
                    STMT("packers.Int32.pack(obj.value, stream)")
                with BLOCK("else"):
                    STMT("packers.Int32.pack(obj, stream)")
            STMT("@classmethod")
            with BLOCK("def unpack(cls, stream)"):
                STMT("return {0}.get_by_value(packers.Int32.unpack(stream))", enum.name)

    def generate_record_class(self, module, rec):
        BLOCK = module.block
        STMT = module.stmt
        SEP = module.sep
        
        if isinstance(rec, compiler.Exception):
            base = "agnos.PackedException"
        else:
            base = "object"
        with BLOCK("class {0}({1})", rec.name, base):
            STMT("_recid = {0}", rec.id)
            STMT("_ATTRS = [{0}]", ", ".join(repr(mem.name) for mem in rec.members))
            SEP()
            args = ["%s = None" % (mem.name,) for mem in rec.members]
            with BLOCK("def __init__(self, {0})", ", ".join(args)):
                if not rec.members:
                    STMT("pass")
                for mem in rec.members:
                    STMT("self.{0} = {0}", mem.name)
            SEP()
            with BLOCK("def __repr__(self)"):
                attrs = ["self.%s" % (mem.name,) for mem in rec.members]
                STMT("attrs = [{0}]", ", ".join(attrs))
                STMT("return '{0}(%s)' % (', '.join(repr(a) for a in attrs),)", rec.name)

    def generate_record_packer(self, module, rec):
        BLOCK = module.block
        STMT = module.stmt
        SEP = module.sep
        
        with BLOCK("class {0}Packer(packers.Packer)", rec.name):
            STMT("@classmethod")
            with BLOCK("def get_id(cls)"):
                STMT("return {0}", rec.id)

            STMT("@classmethod")
            with BLOCK("def pack(cls, obj, stream)"):
                if not rec.members:
                    STMT("pass")
                for mem in rec.members:
                    STMT("{0}.pack(obj.{1}, stream)", type_to_packer(mem.type), mem.name)

            STMT("@classmethod")
            with BLOCK("def unpack(cls, stream)"):
                with BLOCK("return {0}", rec.name, prefix = "(", suffix = ")"):
                    for mem in rec.members:
                        STMT("{0}.unpack(stream),", type_to_packer(mem.type))
    
    def generate_class_proxy(self, module, service, cls):
        BLOCK = module.block
        STMT = module.stmt
        SEP = module.sep
        DOC = module.doc
        with BLOCK("class {0}Proxy(agnos.BaseProxy)", cls.name):
            STMT("__slots__ = []")
            SEP()
            for attr in cls.all_attrs:
                accessors = []
                if attr.get:
                    with BLOCK("def _get_{0}(self)", attr.name):
                        STMT("return self._client._funcs.sync_{0}(self)", attr.getter.id)
                    accessors.append("_get_%s" % (attr.name,))
                if attr.set:
                    with BLOCK("def _set_{0}(self, value)", attr.name):
                        STMT("self._client._funcs.sync_{0}(self, value)", attr.getter.id)
                    accessors.append("_set_%s" % (attr.name,))
                STMT("{0} = property({1})", attr.name, ", ".join(accessors))
            SEP()
            for method in cls.all_methods:
                if not method.clientside:
                    continue
                args = ", ".join(arg.name for arg in method.args)
                with BLOCK("def {0}(self, {1})", method.name, args):
                    callargs = ["self"] + [arg.name for arg in method.args]
                    STMT("return self._client._funcs.sync_{0}({1})", method.func.id, ", ".join(callargs))
            if cls.all_derived:
                SEP()
                DOC("downcasts")
                for cls2 in cls.all_derived:
                    with BLOCK("def cast_to_{0}()", cls2.name):
                        STMT("return {0}Proxy(self._client, self._objref, False)", cls2.name)
            if cls.all_bases:
                SEP()
                DOC("upcasts")
                for cls2 in cls.all_bases:
                    with BLOCK("def cast_to_{0}()", cls2.name):
                        STMT("return {0}Proxy(self._client, self._objref, False)", cls2.name)

    def generate_handler_interface(self, module, service):
        BLOCK = module.block
        STMT = module.stmt
        SEP = module.sep
        with BLOCK("class IHandler(object)"):
            STMT("__slots__ = []")
            for member in service.funcs.values():
                if isinstance(member, compiler.Func):
                    args = ", ".join(arg.name for arg in member.args)
                    with BLOCK("def {0}(self, {1})", member.fullname, args):
                        STMT("raise NotImplementedError()")

    def generate_processor(self, module, service):
        BLOCK = module.block
        STMT = module.stmt
        SEP = module.sep
        with BLOCK("class Processor(agnos.BaseProcessor)"):
            with BLOCK("def __init__(self, handler, exception_map = {})"):
                STMT("agnos.BaseProcessor.__init__(self)")
                for func in service.funcs.values():
                    self._generate_processor_function(module, func)
                    self._generate_processor_unpacker(module, func)
                SEP()
                STMT("self.exception_map = exception_map")
                STMT("storer = self.store")
                STMT("loader = self.load")
                for tp in service.types.values():
                    if isinstance(tp, compiler.Class):
                        STMT("{0}ObjRef = packers.ObjRef({1}, storer, loader)", tp.name, tp.id)
                SEP()
                STMT("packers_map = {}")
                STMT("heteroMapPacker = packers.HeteroMapPacker(999, packers_map)")
                STMT("packers_map[999] = heteroMapPacker")
                SEP()
                for member in service.types.values():
                    if isinstance(member, compiler.Record):
                        if is_complex_type(member):
                            self.generate_record_packer(module, member)
                            SEP()
                self.generate_templated_packers(module, service)
                SEP()
                with BLOCK("self.func_mapping = ", prefix = "{", suffix = "}"):
                    for func in service.funcs.values():
                        STMT("{0} : (_func_{0}, _unpack_{0}, {1}),", 
                            func.id, type_to_packer(func.type))
                SEP()
                with BLOCK("self.packed_exceptions = ", prefix = "{", suffix = "}"):
                    for mem in service.types.values():
                        if isinstance(mem, compiler.Exception):
                            STMT("{0} : {1},", mem.name, type_to_packer(mem))
                SEP()
                for tp in service.types.values():
                    STMT("packers_map[{0}] = {1}", tp.id, type_to_packer(tp))
            SEP()
            with BLOCK("def process_get_general_info(self, info)"):
                STMT('info["AGNOS_VERSION"] = AGNOS_VERSION')
                STMT('info["IDL_MAGIC"] = IDL_MAGIC')
                STMT('info["SERVICE_NAME"] = "{0}"', service.name)
                STMT('info.add("SUPPORTED_VERSIONS", packers.Str, SUPPORTED_VERSIONS, packers.list_of_str)')
            SEP()
            with BLOCK("def process_get_functions_info(self, info)"):
                for func in service.funcs.values():
                    STMT("funcinfo = utils.HeteroMap()")
                    STMT('funcinfo["name"] = "{0}"', func.name)
                    STMT('funcinfo["type"] = "{0}"', str(func.type))
                    STMT("args = utils.HeteroMap()")
                    for arg in func.args:
                        STMT('args["{0}"] = "{1}"', arg.name, str(arg.type))
                    STMT('funcinfo.add("args", packers.Str, args, packers.BuiltinHeteroMapPacker)')
                    if func.annotations:
                        with BLOCK("anno = ", prefix = "{", suffix = "}"):
                            STMT('"{0}" : "{1}",', anno.name, repr(anno.value))
                        STMT('funcinfo.add("annotations", packers. anno, packers.map_of_str_str)')
                    STMT('info.add({0}, packers.Int32, funcinfo, packers.BuiltinHeteroMapPacker)', func.id)
            SEP()
            with BLOCK("def process_get_function_codes(self, info)"):
                for func in service.funcs.values():
                    STMT('info["{0}"] = {1}', func.name, func.id)    
    
    def _generate_processor_function(self, module, func):
        BLOCK = module.block
        STMT = module.stmt
        SEP = module.sep
        
        if isinstance(func, compiler.Func):
            with BLOCK("def _func_{0}(args)", func.id):
                STMT("return handler.{0}(*args)", func.fullname)
        else:
            with BLOCK("def _func_{0}(args)", func.id):
                STMT("obj = args.pop(0)")
                if isinstance(func.origin, compiler.ClassAttr):
                    if func.type == compiler.t_void:
                        # setter
                        STMT("obj.{0} = args[0]", func.origin.name)
                    else:
                        # getter
                        STMT("return obj.{0}", func.origin.name)
                else:
                    STMT("return obj.{0}(*args)", func.origin.name)

    def _generate_processor_unpacker(self, module, func):
        BLOCK = module.block
        STMT = module.stmt
        SEP = module.sep
        
        with BLOCK("def _unpack_{0}(transport)", func.id):
            if not func.args:
                STMT("return []")
                return 
            with BLOCK("return ", prefix = "[", suffix="]"):
                for arg in func.args:
                    STMT("{0}.unpack(transport),", type_to_packer(arg.type))
    
    def generate_client(self, module, service):
        BLOCK = module.block
        STMT = module.stmt
        SEP = module.sep

        with BLOCK("class Client(agnos.BaseClient)"):
            with BLOCK("def __init__(self, transport)"):
                self.generate_client_ctor(module, service)
            SEP()
            for func in service.funcs.values():
                if not isinstance(func, compiler.Func) or func.namespace or not func.clientside:
                    continue
                args = ", ".join(arg.name for arg in func.args)
                with BLOCK("def {0}(_self, {1})", func.name, args):
                    STMT("return _self._funcs.sync_{0}({1})", func.id, args)
            SEP()
            self.generate_client_helpers(module, service)

    def generate_client_ctor(self, module, service):
        BLOCK = module.block
        STMT = module.stmt
        SEP = module.sep

        for member in service.types.values():
            if isinstance(member, compiler.Record):
                if is_complex_type(member):
                    self.generate_record_packer(module, member)
                    SEP()
        STMT("packed_exceptions = {}")
        STMT("self._utils = agnos.ClientUtils(transport, packed_exceptions)")
        SEP()
        STMT("storer = lambda proxy: -1 if proxy is None else proxy._objref")
        for tp in service.types.values():
            if isinstance(tp, compiler.Class):
                STMT("{0}ObjRef = packers.ObjRef({1}, storer, partial(self._utils.get_proxy, {0}Proxy, self))", tp.name, tp.id)
        SEP()
        STMT("packers_map = {}")
        STMT("heteroMapPacker = packers.HeteroMapPacker(999, packers_map)")
        STMT("packers_map[999] = heteroMapPacker")
        SEP()
        self.generate_templated_packers(module, service)
        SEP()
        for mem in service.types.values():
            if isinstance(mem, compiler.Exception):
                STMT("packed_exceptions[{0}] = {1}", mem.id, type_to_packer(mem))
        SEP()
        for tp in service.types.values():
            STMT("packers_map[{0}] = {1}", tp.id, type_to_packer(tp))
        SEP()
        with BLOCK("class Functions(object)"):
            with BLOCK("def __init__(self, utils)"):
                STMT("self.utils = utils")
            for func in service.funcs.values():
                args = ", ".join(arg.name for arg in func.args)
                with BLOCK("def sync_{0}(_self, {1})", func.id, args):
                    with BLOCK("with _self.utils.invocation({0}, {1}) as seq", func.id, type_to_packer(func.type)):
                        if not func.args:
                            STMT("pass")
                        else:
                            for arg in func.args:
                                STMT("{0}.pack({1}, _self.utils.transport)", type_to_packer(arg.type), arg.name)
                    STMT("return _self.utils.get_reply(seq)")
        SEP()
        STMT("self._funcs = Functions(self._utils)")
        SEP()
        namespaces = set(func.namespace.split(".")[0] for func in service.funcs.values() 
            if func.namespace)
        for ns in namespaces:
            STMT("self.{0} = agnos.Namespace()", ns.split(".")[0])
        for func in service.funcs.values():
            if not func.namespace or not func.clientside:
                continue
            head, tail = (func.namespace + "." + func.name).split(".", 1)
            STMT("self.{0}['{1}'] = self._funcs.sync_{2}", head, tail, func.id)        

    def generate_client_helpers(self, module, service):
        BLOCK = module.block
        STMT = module.stmt
        SEP = module.sep

        with BLOCK("def assert_service_compatibility(self)"):
            STMT("info = self.get_service_info(agnos.INFO_GENERAL)")
            
            with BLOCK('if info["AGNOS_VERSION"] != AGNOS_VERSION'):
                STMT('''raise agnos.WrongAgnosVersion("expected version '%s' found '%s'" % (AGNOS_VERSION, agnos_version))''')
            with BLOCK('if info["SERVICE_NAME"] != "{0}"', service.name):
                STMT('''raise agnos.WrongServiceName("expected service '{0}', found '%s'" % (info["SERVICE_NAME"],))''', service.name)
            with BLOCK('if CLIENT_VERSION'):
                STMT('supported_versions = info.get("SUPPORTED_VERSIONS", None)')
                with BLOCK('if not supported_versions or CLIENT_VERSION not in supported_versions'):
                    STMT('''raise agnos.IncompatibleServiceVersion("server does not support client version '%s'" % (CLIENT_VERSION,))''')























