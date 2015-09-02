Tests to verify docker-compile.pl

docker-compile.pl is a tool to build a docker image like `docker build`, but while only generating one layer per Dockerfile instead of one layer per Dockerfile line.
This is a suite of tests to verify that it behaves identically to `docker build`.
