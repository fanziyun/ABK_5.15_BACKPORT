// SPDX-License-Identifier: GPL-2.0
/*
 * scx_loader -- attach a sched_ext scheduler and mark tasks for it.
 *
 * This is the other half of S3: abk_scx_min.bpf.c is the scheduler, this is the
 * program that binds it to the class Batches 54-60 landed and hands it tasks.
 *
 * Subcommands (deliberately explicit -- no subcommand prints usage and exits 2,
 * so nothing can attach a scheduler by accident):
 *
 *   selftest <obj>            open the BPF object and describe it; no kernel
 *                             interaction at all, so it is the build-host check
 *   status                    print /sys/kernel/debug/sched/ext
 *   run <obj> [--pid N]...    load + attach, then set SCHED_EXT on the pids
 *   mark <pid>...             SCHED_EXT on already-running pids
 *   unmark <pid>...           back to SCHED_NORMAL
 *
 * The attach is dropped on exit (SIGINT/SIGTERM included) and any pid this
 * program marked is handed back to SCHED_NORMAL, so a scheduler crash cannot
 * leave tasks stranded on a class with no scheduler -- the kernel would fall
 * back to the built-in classes anyway, but unmarking keeps the task's policy
 * honest.
 *
 * Partial by construction: this program never asks for the whole machine.  The
 * scheduler's ops are the ones that decide that (abk_scx_min never calls
 * scx_bpf_switch_all()), and only the pids passed on the command line are
 * marked, so the blast radius is a task list the caller chose.
 */
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <bpf/bpf.h>
#include <bpf/libbpf.h>
#include <sched.h>

/* bionic and older glibc do not know this one; include/uapi/linux/sched.h: 7. */
#ifndef SCHED_EXT
#define SCHED_EXT 7
#endif

/*
 * glibc spells the default policy SCHED_OTHER only; the kernel and bionic also
 * know SCHED_NORMAL.  Same value, and this is the one the rest of this module
 * talks about, so the alias is carried here rather than picked per libc.
 */
#ifndef SCHED_NORMAL
#define SCHED_NORMAL 0
#endif

#define EXT_STATE_PATH	"/sys/kernel/debug/sched/ext"

static volatile sig_atomic_t stop;

static void on_signal(int sig)
{
	(void)sig;
	stop = 1;
}

static int log_libbpf(enum libbpf_print_level level, const char *fmt,
		      va_list args)
{
	/* The object's BTF dump at debug level is megabytes of noise. */
	if (level == LIBBPF_DEBUG)
		return 0;
	return vfprintf(stderr, fmt, args);
}

static int set_policy(pid_t pid, int policy)
{
	struct sched_param param = { .sched_priority = 0 };

	if (sched_setscheduler(pid, policy, &param) < 0) {
		fprintf(stderr, "scx_loader: sched_setscheduler(%d, %d): %s\n",
			(int)pid, policy, strerror(errno));
		return -1;
	}
	return 0;
}

/*
 * The struct_ops map is found by name; without one the object is not a
 * sched_ext scheduler and there is nothing to attach.
 */
static struct bpf_map *find_ops_map(struct bpf_object *obj, const char *name)
{
	struct bpf_map *map;

	bpf_object__for_each_map(map, obj) {
		if (bpf_map__type(map) != BPF_MAP_TYPE_STRUCT_OPS)
			continue;
		if (!name || !strcmp(bpf_map__name(map), name))
			return map;
	}
	return NULL;
}

static int cmd_selftest(const char *path)
{
	struct bpf_object *obj;
	struct bpf_map *map, *ops;
	struct bpf_program *prog;
	int err = 0;

	obj = bpf_object__open_file(path, NULL);
	if (!obj) {
		fprintf(stderr, "scx_loader: cannot open %s: %s\n", path,
			strerror(errno));
		return 1;
	}

	printf("object: %s\n", bpf_object__name(obj));

	bpf_object__for_each_map(map, obj) {
		printf("map:    %-24s type=%d key=%u value=%u\n",
		       bpf_map__name(map), (int)bpf_map__type(map),
		       bpf_map__key_size(map), bpf_map__value_size(map));
	}
	bpf_object__for_each_program(prog, obj) {
		printf("prog:   %-24s section=%s\n",
		       bpf_program__name(prog), bpf_program__section_name(prog));
	}

	ops = find_ops_map(obj, NULL);
	if (!ops) {
		fprintf(stderr,
			"scx_loader: %s carries no struct_ops map; it is not a "
			"sched_ext scheduler\n", path);
		err = 1;
	} else {
		printf("ops map: %s\n", bpf_map__name(ops));
	}

	bpf_object__close(obj);
	return err;
}

static int cmd_status(void)
{
	char buf[4096];
	ssize_t n;
	int fd;

	fd = open(EXT_STATE_PATH, O_RDONLY);
	if (fd < 0) {
		fprintf(stderr, "scx_loader: %s: %s\n", EXT_STATE_PATH,
			strerror(errno));
		return 1;
	}
	while ((n = read(fd, buf, sizeof(buf))) > 0)
		fwrite(buf, 1, (size_t)n, stdout);
	close(fd);
	return 0;
}

static int cmd_run(const char *path, const char *ops_name, pid_t *pids,
		   int nr_pids)
{
	struct bpf_object *obj;
	struct bpf_map *ops;
	struct bpf_link *link;
	int i, err = 0;

	obj = bpf_object__open_file(path, NULL);
	if (!obj) {
		fprintf(stderr, "scx_loader: cannot open %s: %s\n", path,
			strerror(errno));
		return 1;
	}

	err = bpf_object__load(obj);
	if (err) {
		fprintf(stderr, "scx_loader: loading %s failed: %s\n", path,
			strerror(-err));
		bpf_object__close(obj);
		return 1;
	}

	ops = find_ops_map(obj, ops_name);
	if (!ops) {
		fprintf(stderr, "scx_loader: %s has no struct_ops map%s%s\n",
			path, ops_name ? " named " : "",
			ops_name ? ops_name : "");
		bpf_object__close(obj);
		return 1;
	}

	link = bpf_map__attach_struct_ops(ops);
	if (!link) {
		fprintf(stderr, "scx_loader: attaching %s failed: %s\n",
			bpf_map__name(ops), strerror(errno));
		bpf_object__close(obj);
		return 1;
	}
	printf("scx_loader: attached %s; %d pid(s) marked SCHED_EXT\n",
	       bpf_map__name(ops), nr_pids);

	for (i = 0; i < nr_pids && !stop; i++)
		set_policy(pids[i], SCHED_EXT);

	signal(SIGINT, on_signal);
	signal(SIGTERM, on_signal);
	while (!stop)
		pause();

	printf("scx_loader: detaching %s\n", bpf_map__name(ops));
	for (i = 0; i < nr_pids; i++)
		set_policy(pids[i], SCHED_NORMAL);

	bpf_link__destroy(link);
	bpf_object__close(obj);
	return 0;
}

static void usage(const char *argv0)
{
	fprintf(stderr,
		"usage: %s selftest <obj>\n"
		"       %s status\n"
		"       %s run <obj> [--ops NAME] [--pid N]...\n"
		"       %s mark <pid>...\n"
		"       %s unmark <pid>...\n",
		argv0, argv0, argv0, argv0, argv0);
}

int main(int argc, char **argv)
{
	pid_t pids[64];
	int nr_pids = 0;
	const char *ops_name = NULL;
	int i;

	libbpf_set_print(log_libbpf);

	if (argc < 2) {
		usage(argv[0]);
		return 2;
	}

	if (!strcmp(argv[1], "selftest")) {
		if (argc != 3) { usage(argv[0]); return 2; }
		return cmd_selftest(argv[2]);
	}
	if (!strcmp(argv[1], "status"))
		return cmd_status();

	if (!strcmp(argv[1], "mark") || !strcmp(argv[1], "unmark")) {
		int policy = argv[1][0] == 'm' ? SCHED_EXT : SCHED_NORMAL;

		if (argc < 3) { usage(argv[0]); return 2; }
		for (i = 2; i < argc; i++)
			if (set_policy((pid_t)atoi(argv[i]), policy))
				return 1;
		return 0;
	}

	if (strcmp(argv[1], "run") || argc < 3) {
		usage(argv[0]);
		return 2;
	}

	for (i = 3; i < argc; i++) {
		if (!strcmp(argv[i], "--ops") && i + 1 < argc) {
			ops_name = argv[++i];
		} else if (!strcmp(argv[i], "--pid") && i + 1 < argc) {
			if (nr_pids == (int)(sizeof(pids) / sizeof(pids[0]))) {
				fprintf(stderr, "scx_loader: too many --pid\n");
				return 2;
			}
			pids[nr_pids++] = (pid_t)atoi(argv[++i]);
		} else {
			usage(argv[0]);
			return 2;
		}
	}

	return cmd_run(argv[2], ops_name, pids, nr_pids);
}
