<?php
/** One-use Boron webmail launch exchange. */
class boron_sso extends rcube_plugin
{
    public $task = 'login|logout|mail';
    private $socket = '/var/www/roundcube/run/boron-launch.sock';
    private $launch_id = 0;

    public function init()
    {
        $this->add_hook('authenticate', [$this, 'authenticate']);
        $this->add_hook('login_after', [$this, 'login_after']);
        // session_destroy runs before Roundcube clears $_SESSION. The
        // logout_after hook is too late to recover our launch id.
        $this->add_hook('session_destroy', [$this, 'session_destroy']);
    }

    private function exchange(array $request)
    {
        $error = 0;
        $message = '';
        $client = @stream_socket_client('unix://' . $this->socket, $error, $message, 3);
        if (!$client) {
            error_log('Boron SSO socket unavailable: ' . (int) $error . ' ' . substr((string) $message, 0, 160));
            return null;
        }
        stream_set_timeout($client, 3);
        fwrite($client, json_encode($request, JSON_UNESCAPED_SLASHES) . "\n");
        $line = fgets($client, 4096);
        fclose($client);
        $reply = is_string($line) ? json_decode($line, true) : null;
        return is_array($reply) ? $reply : null;
    }

    public function authenticate($args)
    {
        $token = rcube_utils::get_input_value('_boron_token', rcube_utils::INPUT_POST, true);
        if (!is_string($token) || $token === '') {
            return $args;
        }
        $reply = $this->exchange([
            'action' => 'redeem',
            'token' => $token,
            'source_ip' => $_SERVER['REMOTE_ADDR'] ?? null,
        ]);
        if (!$reply || empty($reply['ok']) || empty($reply['result']['username'])
            || empty($reply['result']['password']) || empty($reply['result']['launch_id'])) {
            $reason = is_array($reply) && isset($reply['error']) ? (string) $reply['error'] : 'unreadable exchange response';
            error_log('Boron SSO exchange rejected: ' . substr($reason, 0, 160));
            $args['abort'] = true;
            $args['error'] = 'Boron webmail link expired. Return to the panel and open webmail again.';
            return $args;
        }
        $args['user'] = $reply['result']['username'];
        $args['pass'] = $reply['result']['password'];
        $args['host'] = '127.0.0.1';
        // The redeemed one-use token is the request authenticity proof for
        // this login. Roundcube's ordinary form token cannot be read by the
        // panel across origins, so mark only this successfully exchanged
        // request as valid.
        $args['valid'] = true;
        $args['abort'] = false;
        $args['cookiecheck'] = false;
        $this->launch_id = (int) $reply['result']['launch_id'];
        return $args;
    }

    public function login_after($args)
    {
        // Persist only after Roundcube has established and regenerated the
        // authenticated session. Values written by authenticate() can be lost
        // during that transition in Roundcube 1.7.
        if ($this->launch_id > 0) {
            $_SESSION['boron_launch_id'] = $this->launch_id;
        }
        return $args;
    }

    public function session_destroy($args)
    {
        $launch_id = (int) ($_SESSION['boron_launch_id'] ?? 0);
        if ($launch_id > 0) {
            $this->exchange([
                'action' => 'revoke',
                'launch_id' => $launch_id,
                'source_ip' => $_SERVER['REMOTE_ADDR'] ?? null,
            ]);
        }
        return $args;
    }
}
