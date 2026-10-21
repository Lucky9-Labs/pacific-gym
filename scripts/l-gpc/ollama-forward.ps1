$ProgressPreference='SilentlyContinue'
$ErrorActionPreference='Stop'
try {
 $req=[Console]::In.ReadToEnd() | ConvertFrom-Json
 if ($req.path -notmatch '^/api/(tags|ps|version|chat|generate|show)$') { throw 'Unsupported Ollama endpoint' }
 $params=@{Uri=('http://127.0.0.1:11435'+$req.path);Method=$req.method;TimeoutSec=300;UseBasicParsing=$true}
 if ($req.method -eq 'POST') {$params.Body=[string]$req.body;$params.ContentType='application/json'}
 $response=Invoke-WebRequest @params
 [Console]::Out.Write($response.Content)
} catch { [Console]::Error.Write($_.Exception.Message);exit 1 }
