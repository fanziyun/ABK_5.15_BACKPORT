/* SPDX-License-Identifier: GPL-2.0 */
/*
 * The doubly-linked list libbpf uses, for the Android loader build.
 *
 * Same reason as linux/err.h (see that file): the kernel's tools/include
 * version is written in kernel macros that do not survive a bionic compile.
 * Only the operations libbpf actually calls are provided, and they are the
 * classic ones -- nothing here is Android-specific except the include guard.
 */
#ifndef __ABK_ANDROID_LINUX_LIST_H
#define __ABK_ANDROID_LINUX_LIST_H

#include <stddef.h>

struct list_head {
	struct list_head *next, *prev;
};

#define LIST_HEAD_INIT(name) { &(name), &(name) }
#define LIST_HEAD(name) struct list_head name = LIST_HEAD_INIT(name)

#ifndef container_of
#define container_of(ptr, type, member) \
	((type *)((char *)(ptr) - offsetof(type, member)))
#endif
#define list_entry(ptr, type, member) container_of(ptr, type, member)
#define list_first_entry(ptr, type, member) \
	list_entry((ptr)->next, type, member)
#define list_last_entry(ptr, type, member) \
	list_entry((ptr)->prev, type, member)
#define list_next_entry(pos, member) \
	list_entry((pos)->member.next, __typeof__(*(pos)), member)
#define list_prev_entry(pos, member) \
	list_entry((pos)->member.prev, __typeof__(*(pos)), member)
#define list_for_each_entry(pos, head, member) \
	for (pos = list_first_entry(head, __typeof__(*pos), member); \
	     &pos->member != (head); \
	     pos = list_entry(pos->member.next, __typeof__(*pos), member))

static inline void INIT_LIST_HEAD(struct list_head *list)
{
	list->next = list;
	list->prev = list;
}

static inline int list_empty(const struct list_head *head)
{
	return head->next == head;
}

static inline void __list_add(struct list_head *new_,
			      struct list_head *prev,
			      struct list_head *next)
{
	next->prev = new_;
	new_->next = next;
	new_->prev = prev;
	prev->next = new_;
}

static inline void list_add(struct list_head *new_, struct list_head *head)
{
	__list_add(new_, head, head->next);
}

static inline void __list_del(struct list_head *prev, struct list_head *next)
{
	next->prev = prev;
	prev->next = next;
}

static inline void list_del(struct list_head *entry)
{
	__list_del(entry->prev, entry->next);
	entry->next = NULL;
	entry->prev = NULL;
}

#endif /* __ABK_ANDROID_LINUX_LIST_H */
