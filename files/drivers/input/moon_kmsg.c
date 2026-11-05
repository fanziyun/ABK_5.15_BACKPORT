// SPDX-License-Identifier: GPL-2.0
/*
 * sailboat_moon_kmsg: dump the kernel log when the power key is held.
 *
 * Overlaid as a new file by scripts/stable_backport.sh; drivers/input/Makefile
 * gains the matching obj-y line in the same step.
 */
#include <linux/kernel.h>
#include <linux/kmsg_dump.h>
#include <linux/module.h>
#include <linux/workqueue.h>
#include <linux/jiffies.h>

#include <linux/cred.h>
#include <linux/file.h>
#include <linux/fs.h>
#include <linux/reboot.h>
#include <linux/input.h>

#include <linux/slab.h>
#include <linux/suspend.h>
#include <linux/delay.h>

#define POWERKEY_DOWN_DELAY_MS 3000	/* 3秒 */
#define LOG_FILE_PATH "/metadata/moom_kernel_kmsg.txt"

static atomic_t moon_key_down = ATOMIC_INIT(0);

static void moon_save_kernel_kmsg(void)
{
	ssize_t ret;
	const struct cred *root_cred;
	const struct cred *saved_cred;
	struct kmsg_dump_iter iter = {0};
	char buf[1024] = {0};
	struct file *f;
	size_t len;

	root_cred = prepare_kernel_cred(NULL);
	saved_cred = override_creds(root_cred);

	f = filp_open(LOG_FILE_PATH, O_WRONLY | O_CREAT | O_TRUNC, 0644);
	if (IS_ERR(f))
		goto out_creds;

	while (kmsg_dump_get_line(&iter, false, buf, sizeof(buf), &len)) {
		ret = kernel_write(f, buf, len, &f->f_pos);
		if (ret < 0 || (size_t)ret != len)
			break;
	}

	vfs_fsync(f, 0);
	filp_close(f, NULL);

out_creds:
	revert_creds(saved_cred);
}

static void moon_powerkey_worker(struct work_struct *work)
{
	if (atomic_read(&moon_key_down))
		moon_save_kernel_kmsg();
}

static DECLARE_DELAYED_WORK(moon_kmsg_worker, moon_powerkey_worker);

static void moon_powerkey_event(struct input_handle *handle,
			unsigned int type, unsigned int code, int value)
{
	if (type != EV_KEY)
		return;

	switch (value) {
	case 0:
		/* 按键状态: 松开 */
		atomic_set(&moon_key_down, 0);
		break;
	case 1:
		/* 按键状态: 按下 */
		atomic_set(&moon_key_down, 1);
		queue_delayed_work(system_highpri_wq, &moon_kmsg_worker,
				   msecs_to_jiffies(POWERKEY_DOWN_DELAY_MS));
		break;
	}
}

static int moon_powerkey_connect(struct input_handler *handler,
			struct input_dev *dev, const struct input_device_id *id)
{
	int error;
	struct input_handle *handle;

	handle = kzalloc(sizeof(struct input_handle), GFP_KERNEL);
	if (!handle)
		return -ENOMEM;

	handle->dev = dev;
	handle->name = "moon_powerkey";
	handle->handler = handler;

	error = input_register_handle(handle);
	if (error)
		goto err2;

	error = input_open_device(handle);
	if (error)
		goto err1;

	return 0;
err1:
	input_unregister_handle(handle);
err2:
	kfree(handle);
	return error;
}

static void moon_powerkey_disconnect(struct input_handle *handle)
{
	input_close_device(handle);
	input_unregister_handle(handle);
	kfree(handle);
}

static const struct input_device_id moon_powerkey_table[] = {
	{
		.flags = INPUT_DEVICE_ID_MATCH_EVBIT | INPUT_DEVICE_ID_MATCH_KEYBIT,
		.evbit = { BIT_MASK(EV_KEY) },
		.keybit = { [BIT_WORD(KEY_POWER)] = BIT_MASK(KEY_POWER) },
	},
	{},
};

static struct input_handler moon_powerkey_event_handler = {
	.name = "moon_powerkey_handler",
	.connect = moon_powerkey_connect,
	.disconnect = moon_powerkey_disconnect,
	.event = moon_powerkey_event,
	.id_table = moon_powerkey_table,
};

static int __init moon_kmsg_init(void)
{
	return input_register_handler(&moon_powerkey_event_handler);
}
module_init(moon_kmsg_init);
