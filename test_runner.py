import functools
import json
import logging
import os
import os.path
import re
import shutil
import subprocess
import unittest

logger = logging.getLogger('TestRunner')

BASEPATH = os.path.dirname(__file__)
DOCKER = '/usr/bin/docker'
COMPILE = os.path.join(BASEPATH, 'docker-compile.pl')

# six.with_metaclass
def with_metaclass(meta, *bases):
	"""Create a base class with a metaclass."""
	# This requires a bit of explanation: the basic idea is to make a dummy
	# metaclass for one level of class instantiation that replaces itself with
	# the actual metaclass.
	class metaclass(meta):
	    def __new__(cls, name, this_bases, d):
	        return meta(name, bases, d)
	return type.__new__(metaclass, 'temporary_class', (), {})

# replace non-method-safe chars with _
method_name_cleaner_re = re.compile('[^a-zA-Z0-9_]')
def clean_method_name(path):
	return method_name_cleaner_re.sub("_", path)

# automatic unit test class based on directories found in a directory
class MetaTestCompare(type):
	def __new__(mcs, name, bases, dict):
		""" The metaclass client class must define:
			subdir - a path with a bunch of docker project directories
		"""
		def gen_test(directory):
			def test(self):
				self.do_docker_compare(directory)
			return test
		testpath = os.path.join(BASEPATH, dict['subdir'])
		dict['testpath'] = testpath
		entries = os.listdir(testpath)
		for entry in entries:
			path = os.path.join(testpath, entry)
			if not os.path.isdir(path):
				continue
			test_name = "test_%s" % (clean_method_name(entry),)
			test = gen_test(path)
			test.__name__ = test_name
			dict[test_name] = test
		return type.__new__(mcs, name, bases, dict)

# build a docker project with both Docker and docker-compile.pl
# look for any differences
class ParentTestCompare(unittest.TestCase):
	def setUp(self):
		self.cwd = self.testpath
		self.cleanup_images = []
	# helper methods to run external commands
	def run_docker(self, *args):
		full_args = [DOCKER]
		full_args.extend(args)
		ret = subprocess.call(full_args, cwd=self.cwd)
		return ret
	def run_docker_build(self, name):
		args = [DOCKER, 'build', '--tag=%s' % (name,), '.']
		ret = subprocess.call(args, cwd=self.cwd)
		self.cleanup_images.append(name)
		self.assertEqual(0, ret)
		return ret
	def run_compile_build(self, name):
		args = [COMPILE, '-t', name]
		ret = subprocess.call(args, cwd=self.cwd)
		self.cleanup_images.append(name)
		self.assertEqual(0, ret)
		return ret
	def load_image_info(self, name):
		args = [DOCKER, 'inspect', name]
		bytes_data = subprocess.check_output(args)
		str_data = bytes_data.decode('utf-8')
		return json.loads(str_data)[0]

	# run the test
	def prepare_names(self):
		self.cwd = self.directory
		self.testdir = self.directory[len(BASEPATH)+1:]
		self.name = clean_method_name(self.testdir)

	def compare_images(self, truth, question):
		truth_data = self.load_image_info(truth)
		question_data = self.load_image_info(question)
		important_keys = [ 'Cmd', 'Entrypoint', 'Env', 'ExposedPorts', 'WorkingDir']
		for key in important_keys:
			self.assertEqual(truth_data['Config'][key], question_data['Config'][key])

	def do_docker_compare(self, directory):
		logger.info("Testing %s"%(directory,))
		self.directory = directory
		self.prepare_names()
		docker_image = "%s_docker" % (self.name,)
		compile_image = "%s_compile" % (self.name,)
		self.run_docker_build(docker_image)
		self.run_compile_build(compile_image)
		self.compare_images(docker_image, compile_image)

	def tearDown(self):
		for image in self.cleanup_images:
			self.run_docker('rmi', '-f', image)

# look for differences in environment handling
class TestCompare(with_metaclass(MetaTestCompare, ParentTestCompare)):
	subdir = os.path.join('compare')

if __name__ == '__main__':
	unittest.main()
