/* Dovecot 2.3 checkpassword userdb guard. Never authenticates a user.
 * Configure before the real SQL userdb, with result_failure=continue and
 * result_internalfail=return-fail. Exit 3 delegates an unblocked lookup;
 * exit 111 asks Dovecot to return a temporary failure during restoration.
 * No credentials, message data, or usernames are written to stdout/stderr.
 */
#define _GNU_SOURCE
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <openssl/sha.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#ifndef BORON_MAIL_RESTORE_GATES
#define BORON_MAIL_RESTORE_GATES "/var/lib/boron/mail-restore-gates"
#endif

int main(void)
{
    const char *authorized = getenv("AUTHORIZED");
    if (authorized == NULL || strcmp(authorized, "1") != 0)
        return 111;

    char user[320];
    size_t length = 0;
    unsigned int separators = 0;
    for (;;) {
        unsigned char byte;
        ssize_t got = read(3, &byte, 1);
        if (got < 0 && errno == EINTR)
            continue;
        if (got != 1)
            return 111;
        if (byte == '\0')
            break;
        if (length == sizeof(user) - 1)
            return 111;
        if (byte == '@')
            separators++;
        else if (!((byte >= 'a' && byte <= 'z') ||
                   (byte >= 'A' && byte <= 'Z') ||
                   (byte >= '0' && byte <= '9') ||
                   byte == '.' || byte == '_' || byte == '-' ||
                   byte == '+' || byte == '%'))
            return 111;
        user[length++] = (char)tolower(byte);
    }
    if (length < 3 || separators != 1 || user[0] == '@' || user[length-1] == '@')
        return 111;
    user[length] = '\0';
    unsigned char digest[SHA256_DIGEST_LENGTH];
    char marker[SHA256_DIGEST_LENGTH * 2 + 1];
    static const char hex[] = "0123456789abcdef";
    if (SHA256((const unsigned char *)user, length, digest) == NULL)
        return 111;
    for (size_t i = 0; i < SHA256_DIGEST_LENGTH; i++) {
        marker[i * 2] = hex[digest[i] >> 4];
        marker[i * 2 + 1] = hex[digest[i] & 15];
    }
    marker[sizeof(marker) - 1] = '\0';

    int directory = open(BORON_MAIL_RESTORE_GATES, O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
    /* The installer creates this directory. Missing/unreadable state must
     * never silently let a partially restored mailbox accept delivery. */
    if (directory < 0)
        return 111;
    struct stat info;
    if (fstat(directory, &info) != 0 || info.st_uid != 0 || (info.st_mode & 0022)) {
        close(directory);
        return 111;
    }
    int found = fstatat(directory, marker, &info, AT_SYMLINK_NOFOLLOW);
    int saved_errno = errno;
    close(directory);
    if (found == 0)
        return 111;
    return saved_errno == ENOENT ? 3 : 111;
}
