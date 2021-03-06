import filecmp
import functools
import json
import logging
import os
import os.path
import re
import shutil
import subprocess
import sys
import tempfile
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
				self.check_directory(directory)
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

def capture_stdout(orig):
	@functools.wraps(orig)
	def func(*args, **kwargs):
		oldstdout = sys.stdout
		with tempfile.TemporaryFile() as tmp:
			sys.stdout = tmp
			try:
				return orig(*args, **kwargs)
			finally:
				sys.stdout = oldstdout
				tmp.flush()
				tmp.seek(0)
				sys.stdout.write(tmp.read().decode('utf-8'))
	return func
# build a docker project with both Docker and docker-compile.pl
# look for any differences
class ParentTestCompare(unittest.TestCase):
	def setUp(self):
		self.cwd = self.testpath
		self.cleanup_images = []
		self.delete_dockerfile = False

	# helper methods to run external commands
	@capture_stdout
	def run_docker(self, *args):
		full_args = [DOCKER]
		full_args.extend(args)
		ret = subprocess.call(full_args, cwd=self.cwd, stdout=sys.stdout, stderr=subprocess.STDOUT)
		return ret
	@capture_stdout
	def run_docker_build(self, name):
		args = [DOCKER, 'build', '--tag=%s' % (name,), '.']
		ret = subprocess.call(args, cwd=self.cwd, stdout=sys.stdout, stderr=subprocess.STDOUT)
		self.cleanup_images.append(name)
		self.assertEqual(0, ret)
		return ret
	@capture_stdout
	def run_compile_build(self, name):
		args = [COMPILE, '-t', name]
		ret = subprocess.call(args, cwd=self.cwd, stdout=sys.stdout, stderr=subprocess.STDOUT)
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

	def prepare_results(self):
		self.results_path = os.path.join(BASEPATH, 'results', self.testdir)
		self.results_truth_path = os.path.join(self.results_path, 'truth')
		self.results_question_path = os.path.join(self.results_path, 'question')
		if os.path.isdir(self.results_truth_path):
			shutil.rmtree(self.results_truth_path)
		if os.path.isdir(self.results_question_path):
			shutil.rmtree(self.results_question_path)
		os.makedirs(self.results_truth_path)
		os.makedirs(self.results_question_path)

	def copy_results(self, image_name, output_dir):
		run_docker = ["run", "--rm"]
		mount = ["-v=%s:/results_out:rw" % (output_dir,)]
		entrypoint = ["--entrypoint=/bin/sh"]
		image = [image_name]
		command = ["test -x /results && cp -r /results/* /results_out"]
		full_command = run_docker + mount + entrypoint + image + command
		self.run_docker(*full_command)

	def compare_images(self, truth, question):
		truth_data = self.load_image_info(truth)
		question_data = self.load_image_info(question)
		if isinstance(truth_data['Config']['Env'], list):
			truth_data['Config']['Env'].sort()
		if isinstance(question_data['Config']['Env'], list):
			question_data['Config']['Env'].sort()
		important_keys = [ 'Cmd', 'Entrypoint', 'Env', 'ExposedPorts', 'WorkingDir']
		for key in important_keys:
			self.assertEqual(truth_data['Config'][key], question_data['Config'][key], key)

	def compare_results(self, truth_dir, question_dir):
		dircmp = filecmp.dircmp(truth_dir, question_dir)
		self.assertEqual([], dircmp.left_only, "Files missing from the compile results")
		self.assertEqual([], dircmp.right_only, "Extra files from the compile results")
		self.assertEqual([], dircmp.diff_files, "Files should not be different")
		dircmp.report()

	def do_single_step(self):
		# there is a single Dockerfile in the directory, run it
		print("self.name here: %s" % (self.name,))
		docker_image = "%s_docker" % (self.name,)
		compile_image = "%s_compile" % (self.name,)
		print("Building %s" % (docker_image,))
		self.run_docker_build(docker_image)
		self.run_compile_build(compile_image)
		self.compare_images(docker_image, compile_image)
		self.copy_results(docker_image, self.results_truth_path)
		self.copy_results(compile_image, self.results_question_path)
		self.compare_results(self.results_truth_path, self.results_question_path)

	def copy_dockerfile(self, filename, from_image):
		oldpath = os.path.join(self.directory, filename)
		newpath = os.path.join(self.directory, "Dockerfile")
		with open(oldpath, 'r') as file:
			contents = file.readlines()
		if from_image:
			contents = [l if not l.upper().startswith("FROM") else "FROM %s" % (from_image,) for l in contents]
		print(contents)
		with open(newpath, 'w') as file:
			file.writelines(contents)
		self.delete_dockerfile = True

	def do_multiple_steps(self):
		entries = os.listdir(self.directory)
		dockerfiles = [e for e in entries if e.lower().startswith("dockerfile")]
		previous_image = None
		index = 0
		for dockerfile in sorted(dockerfiles):
			index = index + 1
			self.name = "%s_%s" % (clean_method_name(self.testdir), index)
			print("self.name out here: %s" % (self.name,))
			self.copy_dockerfile(dockerfile, previous_image)
			self.do_single_step()
			previous_image = "%s_docker" % (self.name,)

	def check_directory(self, directory):
		logger.info("Testing %s"%(directory,))
		self.directory = directory
		self.prepare_names()
		self.prepare_results()
		if os.path.exists(os.path.join(self.directory, 'Dockerfile')) and \
		   not os.path.islink(os.path.join(self.directory, 'Dockerfile')):
			self.do_single_step()
		else:
			self.do_multiple_steps()

	def tearDown(self):
		for image in self.cleanup_images:
			self.run_docker('rmi', '-f', image)
		if self.delete_dockerfile:
			os.unlink(os.path.join(self.directory, 'Dockerfile'))

# look for differences in environment handling
class TestCompare(with_metaclass(MetaTestCompare, ParentTestCompare)):
	subdir = os.path.join('tests')

if __name__ == '__main__':
	unittest.main()
