<?php
/** One-use Boron webmail launch exchange. */
class boron_sso extends rcube_plugin
{
    public $task = 'login|logout|mail';
    private $socket = '/run/boron-webmail/launch.sock';

    public function init()
    {
        $this->add_hook('authenticate', [$this, 'authenticate']);
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
        if (!$reply || empty($reply['ok']) || empty($reply['result']['username']) || empty($reply['result']['password'])) {
            $args['abort'] = true;
            $args['error'] = 'Boron webmail link expired. Return to the panel and open webmail again.';
            return $args;
        }
        $args['user'] = $reply['result']['username'];
        $args['pass'] = $reply['result']['password'];
        $args['host'] = '127.0.0.1';
        $args['cookiecheck'] = false;
        rcmail::get_instance()->session->set('boron_launch_id', (int) $reply['result']['launch_id']);
        return $args;
    }

    public function session_destroy($args)
    {
        $launch_id = (int) rcmail::get_instance()->session->get('boron_launch_id');
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
