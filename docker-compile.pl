#!/usr/bin/env perl

use feature 'switch';
use strict;
use warnings;
no if $] >= 5.018, warnings => "experimental::smartmatch";

use Data::Dumper;
use File::Basename;
use File::Copy;
use File::Path qw/make_path/;
use File::Temp qw/tempdir/;

use Getopt::Long;
use JSON;

our ( $from, $author, %changes, @commands, $tmpdir, $tmpcount, $prefix, $tag, $filename );

$tmpdir = tempdir(CLEANUP => !$ENV{LEAVE_TMPDIR});
$tmpcount = 0;
$prefix = '';

$filename = 'Dockerfile';
GetOptions ('t=s' => \$tag,'f=s' => \$filename);

print "*** Working directory: $tmpdir\n" if $ENV{LEAVE_TMPDIR};

open DOCKERFILE, "<$filename" or die "$!";

while ( <DOCKERFILE> ) {
  chomp;

  # handle long lines
  $_ = "$prefix$_";
  $prefix = '';
  if ( /\\$/ ) {
    s/\\$//;
    $prefix="$_\n";
    next;
  }

  s/^\s*//;
  /^#/ and next;
  /^$/ and next;

  my ($cmd, $args) = split(/\s+/, $_, 2);
  given ( uc $cmd ) {
    # building the image
    when ('FROM') {
      $from = $args;
      #system("docker", "pull", $from);
      system("docker inspect $from 1>/dev/null 2>&1 || docker pull $from");
      open my $inspect_fh, "-|", "docker", "inspect", "$from";
      my $inspect_str = join "", <$inspect_fh>;
      close $inspect_fh;
      #print $inspect_str;
      my $inspect_data = decode_json $inspect_str;
      $inspect_data = $inspect_data->[0];
      if ( $inspect_data->{"Config"}->{"Cmd"} ) {
        $changes{CMD} = encode_json $inspect_data->{"Config"}->{"Cmd"}
      }
      if ( $inspect_data->{"Config"}->{"Entrypoint"} ) {
        $changes{ENTRYPOINT} = encode_json $inspect_data->{"Config"}->{"Entrypoint"};
      }
    }
    when ('RUN')  { push @commands, $args }
    when ('ADD')  {
      $tmpcount++;
      my ( $src, $dest ) = split ' ', $args, 2;

      if ( $src =~ /^https?:/ ) {
        my $basename = basename($src);
        my $target = "$tmpdir/dl/$tmpcount/$basename";
        make_path "$tmpdir/dl/$tmpcount";
        system('wget', '-O', $target, $src) == 0 or die;
        $src = $target;
      }

      my $local = "$tmpdir/$tmpcount";

      given ( $src ) {
        when ( /\.(tar(\.(gz|bz2|xz))?|tgz)$/ ) {
          mkdir $local;
          system('tar', '-C', $local, '-xf', $_) == 0 or die;
          push @commands, "mkdir -p '$dest'", "( cd /.data/$tmpcount ; cp -a . '$dest' )";
        }
        when ( -f $_ ) {
          $dest .= basename($_) if ( $dest =~ /\/$/ );
          system('cp', '-a', $_, $local) == 0 or die;
          push @commands, "mkdir -p '".dirname($dest)."'", "cp -a /.data/$tmpcount '$dest'";
        }
        when ( -d $_ ) {
          # Handle trailing slash combinations properly:
          # - `$src=/dir,  $dest=/foo  -> /foo`
          # - `$src=/dir,  $dest=/foo/ -> /foo/dir`
          # - `$src=/dir/, $dest=/foo  -> /foo`
          # - `$src=/dir/, $dest=/foo/ -> /foo`
          $dest .= basename($_) if ( $_ !~ /\/$/ && $dest =~ /\/$/ );

          system('cp', '-a', $_, $local) == 0 or die;
          push @commands, "mkdir -p '$dest'", "( cd /.data/$tmpcount ; cp -a . '$dest' )";
        }
        default { die }
      }
    }

    # image metadata
    when ('MAINTAINER') { $author = $args }
    when ('CMD')        {
      $changes{CMD} =        eval { $args } || ['sh', '-c', $args];
      unless ( $changes{ENV}->{PATH} ) {
        $changes{ENV}->{PATH} = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin";
      }
    }
    when ('ENTRYPOINT') {
      $changes{ENTRYPOINT} = eval { $args } || ['sh', '-c', $args];
      unless ( $changes{ENV}->{PATH} ) {
        $changes{ENV}->{PATH} = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin";
      }
    }
    when ('WORKDIR')    { $changes{WORKDIR} = $args }
    when ('USER')       { $changes{USER}       = $args }
    when ('EXPOSE')     { push @{ $changes{EXPOSE} ||= [] },   split(' ',$args); }
    when ('ENV')        {
      my ( $k, $v ) = split(/\s+/, $args, 2);
      push @commands, "export $k='$v'";
      $changes{ENV}->{$k} = $v;
    }
    when ('VOLUME')     {
      # This seems to be a NOP in `docker build`.
      # push @{ $metadata{VolumesFrom} ||= [] }, $args
    }
  }
}
close DOCKERFILE;

open SETUP, ">$tmpdir/setup.sh" or die;
print SETUP join("\n", "#!/bin/sh", "set -e -x", @commands), "\ntouch /.data/FINI\n";
close SETUP;
chmod 0755, "$tmpdir/setup.sh";

our @run = ('docker', 'run', "--cidfile=$tmpdir/CID", '-v', "$tmpdir:/.data", $from, "/.data/setup.sh");
print "*** ", join(' ', @run), "\n";
system(@run) == 0 or die;

die "unfinished, not committing\n" unless -f "$tmpdir/FINI";

sleep 1; # docker container is not always immediately up to a commit, let's give it time to cool off.

open CID, "<$tmpdir/CID" or die;
our $cid = <CID>;
close CID;

our @commit = ( 'docker', 'commit' );
push @commit, "--author=$author" if defined $author;
while ( my ($k, $v) = each %changes ) {
  if (ref $v eq 'ARRAY') {
    foreach (@{$v}) {
      push @commit, "--change=$k $_";
    }
  } elsif (ref $v eq 'HASH') {
    while (my ($itemk, $itemv) = each %{$v}) {
      push @commit, "--change=$k $itemk $itemv";
    }
  } else {
    push @commit, "--change=$k $v";
  }
}
push @commit, $cid;
push @commit, $tag if defined $tag;
print "*** ", join(' ', @commit), "\n";
exec(@commit);
